mod sidecar;

use sidecar::{ServerInfo, SidecarState};
use tauri::Manager;

#[tauri::command]
fn get_server_info(state: tauri::State<SidecarState>) -> Option<ServerInfo> {
    state.info.lock().unwrap().clone()
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_opener::init())
        .manage(SidecarState::default())
        .setup(|app| {
            let (info, child) = sidecar::spawn(app.handle())?;
            let state = app.state::<SidecarState>();
            *state.info.lock().unwrap() = Some(info);
            *state.child.lock().unwrap() = Some(child);
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![get_server_info])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
