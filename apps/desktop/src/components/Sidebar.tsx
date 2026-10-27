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
      <div className="brand">
        <span className="brand-mark" aria-hidden="true" />
        <span className="brand-text">
          <strong>Local KB</strong>
          <small>本地知识库</small>
        </span>
      </div>
      <nav className="nav" aria-label="主导航">
        {items.map(({ to, label, icon: Icon }) => (
          <NavLink
            key={to}
            to={to}
            end={to === "/"}
            className={({ isActive }) => `nav-item${isActive ? " active" : ""}`}
            title={label}
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
