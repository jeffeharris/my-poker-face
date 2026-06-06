import { Suspense, useState, type ReactNode } from 'react';
import { useNavigate } from 'react-router-dom';
import { AdminSidebar, type AdminTab } from './AdminSidebar';
import { AdminHeader } from './AdminHeader';
import { AdminMenuContainer } from './AdminMenuContainer';
import { SIDEBAR_ITEMS } from './adminSidebarItems';
import { useViewport } from '../../hooks/useViewport';
import './AdminDashboard.css';
import './AdminShared.css';

const toolFallback = (
  <div style={{ padding: '2rem', textAlign: 'center', color: 'rgba(255,255,255,0.5)' }}>
    Loading…
  </div>
);

interface AdminToolLayoutProps {
  /** Sidebar highlight + breadcrumb tool segment. */
  activeTab: AdminTab;
  title: string;
  subtitle?: string;
  /** Live breadcrumb leaf (e.g. "Capture #42"); see {@link AdminHeader}. */
  leafLabel?: string | null;
  /** Optional docked assistant panel rendered to the right of the content;
   *  its presence switches the grid into the `--with-assistant` layout.
   *  Desktop only — ignored on mobile. */
  assistant?: ReactNode;
  /** Extra nodes rendered as siblings of the layout grid (e.g. full-screen
   *  assistant overlays that sit outside the sidebar/content grid). */
  overlay?: ReactNode;
  /** Mobile chrome: `'menu'` wraps content in {@link AdminMenuContainer}
   *  (title + back); `'bare'` (default) renders content only — for tools that
   *  draw their own header/back affordance. */
  mobileChrome?: 'bare' | 'menu';
  /** Title for the mobile `AdminMenuContainer` header (defaults to `title`). */
  mobileTitle?: string;
  /** Back handler for the mobile `AdminMenuContainer` header. */
  onMobileBack?: () => void;
  /** The tool. Wrapped in `<Suspense>` by the layout. */
  children: ReactNode;
}

/**
 * Shared scaffold for admin tool screens: sidebar + AdminHeader + Suspense'd
 * content on desktop, and the mobile content chrome. Extracted so the route
 * wrappers stop re-implementing the same `admin-dashboard-layout` shell — and
 * so the breadcrumb/header lives in exactly one place.
 */
export function AdminToolLayout({
  activeTab,
  title,
  subtitle,
  leafLabel,
  assistant,
  overlay,
  mobileChrome = 'bare',
  mobileTitle,
  onMobileBack,
  children,
}: AdminToolLayoutProps) {
  const navigate = useNavigate();
  const { isMobile } = useViewport();
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);

  const content = <Suspense fallback={toolFallback}>{children}</Suspense>;

  if (isMobile) {
    return (
      <div className="admin-dashboard-layout admin-dashboard-layout--mobile">
        {mobileChrome === 'menu' ? (
          <AdminMenuContainer title={mobileTitle ?? title} onBack={onMobileBack}>
            <div className="admin-main__content admin-main__content--mobile">{content}</div>
          </AdminMenuContainer>
        ) : (
          <div className="admin-main__content admin-main__content--mobile">{content}</div>
        )}
        {overlay}
      </div>
    );
  }

  return (
    <div
      className={`admin-dashboard-layout ${assistant ? 'admin-dashboard-layout--with-assistant' : ''}`}
    >
      <AdminSidebar
        items={SIDEBAR_ITEMS}
        activeTab={activeTab}
        onTabChange={(tab) => navigate(`/admin/${tab}`, { replace: true })}
        collapsed={sidebarCollapsed}
        onCollapsedChange={setSidebarCollapsed}
      />
      <main className="admin-main">
        <AdminHeader title={title} subtitle={subtitle} leafLabel={leafLabel} />
        <div className="admin-main__content">{content}</div>
      </main>
      {assistant}
      {overlay}
    </div>
  );
}
