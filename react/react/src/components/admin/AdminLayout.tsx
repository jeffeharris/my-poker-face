import { useState, type ReactNode } from 'react';
import {
  ChevronLeft,
  ChevronRight,
  ArrowLeft,
} from 'lucide-react';
import './AdminLayout.css';

export interface AdminNavItem {
  id: string;
  label: string;
  icon: ReactNode;
  description?: string;
}

export interface AdminLayoutProps {
  /** Navigation items for the sidebar */
  navItems: AdminNavItem[];
  /** Currently active nav item id */
  activeItem: string;
  /** Callback when nav item is selected */
  onNavChange: (id: string) => void;
  /** Main content to render */
  children: ReactNode;
  /** Page title shown in header */
  title: string;
  /** Optional subtitle/description */
  subtitle?: string;
  /** Callback when back button is clicked */
  onBack?: () => void;
}

/**
 * AdminLayout - Desktop-focused admin interface framework
 *
 * Features:
 * - Collapsible sidebar navigation
 * - Full-width content area
 * - Desktop-first responsive design
 * - Premium dark aesthetic
 */
export function AdminLayout({
  navItems,
  activeItem,
  onNavChange,
  children,
  title,
  subtitle,
  onBack,
}: AdminLayoutProps) {
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);

  const activeNavItem = navItems.find(item => item.id === activeItem);

  return (
    <div className="admin-layout">
      {/* Sidebar Navigation */}
      <aside
        className={`admin-layout__sidebar ${sidebarCollapsed ? 'admin-layout__sidebar--collapsed' : ''}`}
      >
        {/* Sidebar Header */}
        <div className="admin-layout__sidebar-header">
          {onBack && (
            <button
              className="admin-layout__back-btn"
              onClick={onBack}
              type="button"
              title="Back"
            >
              <ArrowLeft size={18} />
              {!sidebarCollapsed && <span>Back</span>}
            </button>
          )}
          {!sidebarCollapsed && (
            <h1 className="admin-layout__brand">{title}</h1>
          )}
        </div>

        {/* Navigation Items */}
        <nav className="admin-layout__nav">
          {navItems.map(item => (
            <button
              key={item.id}
              className={`admin-layout__nav-item ${activeItem === item.id ? 'admin-layout__nav-item--active' : ''}`}
              onClick={() => onNavChange(item.id)}
              type="button"
              title={sidebarCollapsed ? item.label : undefined}
            >
              <span className="admin-layout__nav-icon">{item.icon}</span>
              {!sidebarCollapsed && (
                <span className="admin-layout__nav-label">{item.label}</span>
              )}
            </button>
          ))}
        </nav>

        {/* Collapse Toggle */}
        <button
          className="admin-layout__collapse-btn"
          onClick={() => setSidebarCollapsed(!sidebarCollapsed)}
          type="button"
          title={sidebarCollapsed ? 'Expand sidebar' : 'Collapse sidebar'}
        >
          {sidebarCollapsed ? <ChevronRight size={16} /> : <ChevronLeft size={16} />}
        </button>
      </aside>

      {/* Main Content Area */}
      <main className="admin-layout__main">
        {/* Content Header */}
        <header className="admin-layout__header">
          <div className="admin-layout__header-content">
            <h2 className="admin-layout__title">
              {activeNavItem?.label || title}
            </h2>
            {(activeNavItem?.description || subtitle) && (
              <p className="admin-layout__subtitle">
                {activeNavItem?.description || subtitle}
              </p>
            )}
          </div>
        </header>

        {/* Content Body */}
        <div className="admin-layout__content">
          {children}
        </div>
      </main>
    </div>
  );
}

export default AdminLayout;
