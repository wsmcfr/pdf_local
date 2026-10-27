import { HashRouter, Route, Routes } from "react-router-dom";
import Sidebar from "./components/Sidebar";
import StatusBar from "./components/StatusBar";
import Chat from "./pages/Chat";
import Jobs from "./pages/Jobs";
import Library from "./pages/Library";
import Onboarding from "./pages/Onboarding";
import Settings from "./pages/Settings";
import "./styles.css";

export default function App() {
  return (
    <HashRouter>
      <div className="app-shell">
        <Sidebar />
        <main className="app-main">
          <Routes>
            <Route path="/" element={<Library />} />
            <Route path="/onboarding" element={<Onboarding />} />
            <Route path="/jobs" element={<Jobs />} />
            <Route path="/chat" element={<Chat />} />
            <Route path="/settings" element={<Settings />} />
          </Routes>
        </main>
        <StatusBar />
      </div>
    </HashRouter>
  );
}
