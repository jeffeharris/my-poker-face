import { type ReactNode } from 'react';
import { useViewport } from '../../hooks/useViewport';
import { MenuBar } from '../shared';
import './AdminMenuContainer.css';

interface AdminMenuContainerProps {
  /** Title shown in mobile header */
  title: string;
  /** Optional subtitle for context */
  subtitle?: string;
  /** Callback when back button pressed (mobile) */
  onBack?: () => void;
  /** Main content to render */
  children: ReactNode;
  /** Optional custom mobile header actions */
  headerActions?: ReactNode;
  /** Optional class name for custom styling */
  className?: string;
  /** Optional: hide the mobile header entirely */
  hideHeader?: boolean;
}

/**
 * AdminMenuContainer - Responsive container for admin menu content
 *
 * Desktop: Transparent pass-through, preserves existing layout
 * Mobile: Full-width container with MenuBar header
 */
export function AdminMenuContainer({
  title,
  onBack,
  children,
  className = '',
  hideHeader = false,
}: AdminMenuContainerProps) {
  const { isMobile } = useViewport();

  // Desktop: transparent pass-through
  if (!isMobile) {
    return <>{children}</>;
  }

  // Mobile: Full-width layout with MenuBar header
  return (
    <div className={`admin-menu-container ${className}`}>
      {!hideHeader && (
        <>
          <MenuBar
            onBack={onBack}
            centerContent={
              <div className="admin-menu-container__header-info">
                <span className="admin-menu-container__header-title">{title}</span>
              </div>
            }
            showUserInfo
            onAdminTools={() => { window.location.href = '/admin'; }}
          />
          {/* Spacer for fixed MenuBar */}
          <div className="menu-bar-spacer" />
        </>
      )}
      <div className="admin-menu-container__content">
        {children}
      </div>
    </div>
  );
}

export default AdminMenuContainer;
