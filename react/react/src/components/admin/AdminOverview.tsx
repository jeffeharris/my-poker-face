import { ChevronRight } from 'lucide-react';
import { SIDEBAR_ITEMS } from './adminSidebarItems';
import type { AdminTab } from './AdminSidebar';
import './AdminOverview.css';

interface AdminOverviewProps {
  onSelect: (tab: AdminTab) => void;
}

/**
 * Desktop landing for /admin: a lightweight grid of tool cards so the dashboard
 * opens instantly instead of auto-mounting a heavy tool. (Mobile has its own
 * menu in AdminDashboard.)
 */
export function AdminOverview({ onSelect }: AdminOverviewProps) {
  return (
    <div className="admin-overview">
      <div className="admin-overview__grid">
        {SIDEBAR_ITEMS.map((item) => (
          <button
            key={item.id}
            className="admin-overview__card"
            onClick={() => onSelect(item.id)}
            type="button"
          >
            <span className="admin-overview__card-icon">{item.icon}</span>
            <span className="admin-overview__card-text">
              <span className="admin-overview__card-title">{item.label}</span>
              <span className="admin-overview__card-desc">{item.description}</span>
            </span>
            <ChevronRight size={18} className="admin-overview__card-arrow" />
          </button>
        ))}
      </div>
    </div>
  );
}
