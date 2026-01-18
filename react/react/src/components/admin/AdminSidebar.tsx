import { useState, useRef, useEffect } from 'react';
import { ChevronLeft, ChevronRight } from 'lucide-react';
import './AdminSidebar.css';

export type AdminTab = 'personalities' | 'analyzer' | 'playground' | 'experiments' | 'models' | 'pricing' | 'templates' | 'settings' | 'debug';

export interface SidebarItem {
  id: AdminTab;
  label: string;
  icon: React.ReactNode;
  description: string;
}

interface AdminSidebarProps {
  items: SidebarItem[];
  activeTab: AdminTab;
  onTabChange: (tab: AdminTab) => void;
  collapsed?: boolean;
  onCollapsedChange?: (collapsed: boolean) => void;
}

export function AdminSidebar({
  items,
  activeTab,
  onTabChange,
  collapsed: controlledCollapsed,
  onCollapsedChange,
}: AdminSidebarProps) {
  const [internalCollapsed, setInternalCollapsed] = useState(false);
  const [tooltipItem, setTooltipItem] = useState<AdminTab | null>(null);
  const [tooltipPosition, setTooltipPosition] = useState({ top: 0 });
  const tooltipTimeoutRef = useRef<number | null>(null);

  const collapsed = controlledCollapsed ?? internalCollapsed;
  const setCollapsed = onCollapsedChange ?? setInternalCollapsed;

  const handleMouseEnter = (item: SidebarItem, event: React.MouseEvent<HTMLButtonElement>) => {
    if (!collapsed) return;

    if (tooltipTimeoutRef.current) {
      clearTimeout(tooltipTimeoutRef.current);
    }

    const rect = event.currentTarget.getBoundingClientRect();
    setTooltipPosition({ top: rect.top + rect.height / 2 });
    setTooltipItem(item.id);
  };

  const handleMouseLeave = () => {
    if (tooltipTimeoutRef.current) {
      clearTimeout(tooltipTimeoutRef.current);
    }
    tooltipTimeoutRef.current = window.setTimeout(() => {
      setTooltipItem(null);
    }, 100);
  };

  useEffect(() => {
    return () => {
      if (tooltipTimeoutRef.current) {
        clearTimeout(tooltipTimeoutRef.current);
      }
    };
  }, []);

  const activeItem = items.find(item => item.id === activeTab);

  return (
    <aside className={`admin-sidebar ${collapsed ? 'admin-sidebar--collapsed' : ''}`}>
      {/* Sidebar Header */}
      <div className="admin-sidebar__header">
        <div className="admin-sidebar__brand">
          <div className="admin-sidebar__brand-icon">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M12 2L2 7l10 5 10-5-10-5z" />
              <path d="M2 17l10 5 10-5" />
              <path d="M2 12l10 5 10-5" />
            </svg>
          </div>
          <span className="admin-sidebar__brand-text">Admin</span>
        </div>
        <button
          className="admin-sidebar__toggle"
          onClick={() => setCollapsed(!collapsed)}
          aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
        >
          {collapsed ? <ChevronRight size={16} /> : <ChevronLeft size={16} />}
        </button>
      </div>

      {/* Navigation Items */}
      <nav className="admin-sidebar__nav">
        <ul className="admin-sidebar__list">
          {items.map((item) => (
            <li key={item.id} className="admin-sidebar__item">
              <button
                className={`admin-sidebar__link ${activeTab === item.id ? 'admin-sidebar__link--active' : ''}`}
                onClick={() => onTabChange(item.id)}
                onMouseEnter={(e) => handleMouseEnter(item, e)}
                onMouseLeave={handleMouseLeave}
                aria-current={activeTab === item.id ? 'page' : undefined}
              >
                <span className="admin-sidebar__link-icon">{item.icon}</span>
                <span className="admin-sidebar__link-text">{item.label}</span>
                {activeTab === item.id && (
                  <span className="admin-sidebar__link-indicator" />
                )}
              </button>
            </li>
          ))}
        </ul>
      </nav>

      {/* Footer with hint */}
      <div className="admin-sidebar__footer">
        <div className="admin-sidebar__footer-hint">
          {collapsed ? '⌘K' : 'Press ⌘K to toggle'}
        </div>
      </div>

      {/* Tooltip (only visible when collapsed) */}
      {collapsed && tooltipItem && (
        <div
          className="admin-sidebar__tooltip"
          style={{ top: tooltipPosition.top }}
        >
          <div className="admin-sidebar__tooltip-content">
            <span className="admin-sidebar__tooltip-label">
              {items.find(i => i.id === tooltipItem)?.label}
            </span>
            <span className="admin-sidebar__tooltip-desc">
              {items.find(i => i.id === tooltipItem)?.description}
            </span>
          </div>
        </div>
      )}
    </aside>
  );
}

export default AdminSidebar;
