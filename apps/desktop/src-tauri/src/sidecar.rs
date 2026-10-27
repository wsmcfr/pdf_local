use std::io::{BufRead, BufReader};
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;

use serde::Serialize;
use tauri::{AppHandle, Manager};

#[derive(Clone, Serialize)]
pub struct ServerInfo {
    pub base_url: String,
    pub token: String,
}

#[derive(Default)]
pub struct SidecarState {
    pub child: Mutex<Option<Child>>,
    pub info: Mutex<Option<ServerInfo>>,
}

impl Drop for SidecarState {
    fn drop(&mut self) {
        if let Some(mut child) = self.child.lock().unwrap().take() {
            let _ = child.kill();
        }
    }
}

fn api_dir() -> PathBuf {
    let manifest = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    manifest.join("../../../services/api")
}

fn dev_python(api: &PathBuf) -> Command {
    // Prefer the project virtualenv (Python 3.11) so the backend dependencies are available.
    let venv_python = api.join("../../../.venv/Scripts/python.exe");
    if venv_python.exists() {
        let mut command = Command::new(venv_python);
        command.arg("run_server.py").current_dir(api);
        command
    } else {
        let mut command = Command::new("py");
        command.args(["-3.11", "run_server.py"]).current_dir(api);
        command
    }
}

pub fn spawn(app: &AppHandle) -> Result<(ServerInfo, Child), String> {
    let token = uuid::Uuid::new_v4().to_string();
    let api = api_dir();

    let mut cmd = if cfg!(debug_assertions) {
        let mut c = dev_python(&api);
        c.env("PYTHONPATH", &api);
        c
    } else {
        let resource_dir = app.path().resource_dir().map_err(|e| e.to_string())?;
        let exe = resource_dir
            .join("binaries")
            .join("kb-server")
            .join("kb-server.exe");
        if !exe.exists() {
            return Err(format!(
                "kb-server sidecar not found at {} (run scripts/build-sidecar.ps1 before packaging)",
                exe.display()
            ));
        }
        Command::new(exe)
    };

    cmd.env("KB_TOKEN", &token)
        .stdout(Stdio::piped())
        .stderr(Stdio::inherit());

    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        cmd.creation_flags(0x08000000); // CREATE_NO_WINDOW
    }

    let mut child = cmd.spawn().map_err(|e| e.to_string())?;
    let stdout = child.stdout.take().ok_or("sidecar stdout missing")?;
    let mut reader = BufReader::new(stdout);

    let mut port = None;
    for line in (&mut reader).lines() {
        let line = line.map_err(|e| e.to_string())?;
        if let Some(payload) = line.strip_prefix("KB_READY ") {
            let value: serde_json::Value =
                serde_json::from_str(payload).map_err(|e| e.to_string())?;
            port = value.get("port").and_then(|v| v.as_u64());
            break;
        }
    }

    let port = port.ok_or("sidecar did not report a port")?;
    let info = ServerInfo {
        base_url: format!("http://127.0.0.1:{port}"),
        token,
    };

    // Keep the stdout reader alive in a background thread so the pipe never blocks.
    std::thread::spawn(move || {
        for _ in reader.lines() {}
    });

    Ok((info, child))
}
