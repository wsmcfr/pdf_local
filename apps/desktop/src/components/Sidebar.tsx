import { BookOpen, Compass, MessageSquare, Settings as SettingsIcon } from "lucide-react";
import { NavLink } from "react-router-dom";

const items = [
  { to: "/", label: "资料库", icon: BookOpen },
  { to: "/onboarding", label: "开始", icon: Compass },
  { to: "/chat", label: "问答", icon: MessageSquare },
  { to: "/settings", label: "设置", icon: SettingsIcon },
];

export default function Sidebar() {
  return (
    <aside className="sidebar">
      <div className="brand">Local KB</div>
      <nav className="nav">
        {items.map(({ to, label, icon: Icon }) => (
          <NavLink
            key={to}
            to={to}
            end={to === "/"}
            className={({ isActive }) => `nav-item${isActive ? " active" : ""}`}
          >
            <Icon size={16} aria-hidden="true" />
            <span>{label}</span>
          </NavLink>
        ))}
      </nav>
      <div className="sidebar-footer">本地知识库 · 离线优先</div>
    </aside>
  );
}
