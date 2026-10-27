import {
  BookOpen,
  Compass,
  MessageSquare,
  PanelLeftClose,
  PanelLeftOpen,
  Settings as SettingsIcon,
} from "lucide-react";
import { NavLink } from "react-router-dom";

const items = [
  { to: "/", label: "资料库", icon: BookOpen },
  { to: "/onboarding", label: "开始", icon: Compass },
  { to: "/chat", label: "问答", icon: MessageSquare },
  { to: "/settings", label: "设置", icon: SettingsIcon },
];

type Props = {
  collapsed: boolean;
  onToggle: () => void;
};

export default function Sidebar({ collapsed, onToggle }: Props) {
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
            aria-label={label}
            title={label}
          >
            <Icon size={16} aria-hidden="true" />
            <span>{label}</span>
          </NavLink>
        ))}
      </nav>
      <div className="sidebar-footer">
        <span className="sidebar-note">本地知识库 · 离线优先</span>
        <button
          type="button"
          className="sidebar-toggle"
          onClick={onToggle}
          aria-label={collapsed ? "展开侧栏" : "收起侧栏"}
          aria-expanded={!collapsed}
          title={collapsed ? "展开侧栏" : "收起侧栏"}
        >
          {collapsed ? (
            <PanelLeftOpen size={15} aria-hidden="true" />
          ) : (
            <PanelLeftClose size={15} aria-hidden="true" />
          )}
        </button>
      </div>
    </aside>
  );
}
