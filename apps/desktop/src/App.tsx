import { useEffect, useState } from "react";
import { HashRouter, Route, Routes } from "react-router-dom";
import Sidebar from "./components/Sidebar";
import StatusBar from "./components/StatusBar";
import Chat from "./pages/Chat";
import Jobs from "./pages/Jobs";
import Library from "./pages/Library";
import Onboarding from "./pages/Onboarding";
import Settings from "./pages/Settings";
import "./styles.css";

const SIDEBAR_KEY = "local-kb.sidebar";

function readSidebarCollapsed(): boolean {
  try {
    return window.localStorage.getItem(SIDEBAR_KEY) === "collapsed";
  } catch {
    return false;
  }
}

export default function App() {
  const [sidebarCollapsed, setSidebarCollapsed] = useState(readSidebarCollapsed);

  useEffect(() => {
    try {
      window.localStorage.setItem(
        SIDEBAR_KEY,
        sidebarCollapsed ? "collapsed" : "expanded",
      );
    } catch {
      // Storage is optional; the toggle still works for this session.
    }
  }, [sidebarCollapsed]);

  return (
    <HashRouter>
      <div
        className={`app-shell${sidebarCollapsed ? " sidebar-collapsed" : ""}`}
      >
        <Sidebar
          collapsed={sidebarCollapsed}
          onToggle={() => setSidebarCollapsed((value) => !value)}
        />
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
