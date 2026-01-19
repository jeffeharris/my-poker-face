import { ReactNode, useState } from 'react';
import { ArrowLeft, Menu, Check } from 'lucide-react';
import { useViewport } from '../../hooks/useViewport';
import { MobileFilterSheet } from './shared/MobileFilterSheet';
import './AdminMenuContainer.css';

interface NavItem {
  id: string;
  label: string;
  icon: ReactNode;
  description: string;
}

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
  /** Navigation items for mobile menu */
  navItems?: NavItem[];
  /** Currently active nav item id */
  activeNavId?: string;
  /** Callback when nav item selected */
  onNavChange?: (id: string) => void;
}

/**
 * AdminMenuContainer - Responsive container for admin menu content
 *
 * Desktop: Transparent pass-through, preserves existing layout
 * Mobile: Full-width container with custom header, menu button, and navigation sheet
 */
export function AdminMenuContainer({
  title,
  subtitle,
  onBack,
  children,
  headerActions,
  className = '',
  hideHeader = false,
  navItems,
  activeNavId,
  onNavChange,
}: AdminMenuContainerProps) {
  const { isMobile } = useViewport();
  const [menuOpen, setMenuOpen] = useState(false);

  // Desktop: transparent pass-through
  if (!isMobile) {
    return <>{children}</>;
  }

  const handleNavSelect = (id: string) => {
    onNavChange?.(id);
    setMenuOpen(false);
  };

  // Mobile: Full-width layout with header
  return (
    <div className={`admin-menu-container ${className}`}>
      {!hideHeader && (
        <header className="admin-menu-container__header">
          <div className="admin-menu-container__header-left">
            {onBack && (
              <button
                className="admin-menu-container__back-btn"
                onClick={onBack}
                aria-label="Go back"
              >
                <ArrowLeft size={20} />
              </button>
            )}
            <div className="admin-menu-container__header-text">
              <h1 className="admin-menu-container__title">{title}</h1>
              {subtitle && (
                <p className="admin-menu-container__subtitle">{subtitle}</p>
              )}
            </div>
          </div>
          <div className="admin-menu-container__header-actions">
            {headerActions}
            {navItems && navItems.length > 0 && (
              <button
                className="admin-menu-container__menu-btn"
                onClick={() => setMenuOpen(true)}
                aria-label="Open menu"
              >
                <Menu size={20} />
              </button>
            )}
          </div>
        </header>
      )}
      <div className="admin-menu-container__content">
        {children}
      </div>

      {/* Mobile Navigation Sheet */}
      {navItems && (
        <MobileFilterSheet
          isOpen={menuOpen}
          onClose={() => setMenuOpen(false)}
          title="Admin Tools"
        >
          <div className="admin-menu-container__nav-list">
            {navItems.map((item) => (
              <button
                key={item.id}
                className={`admin-menu-container__nav-item ${
                  activeNavId === item.id ? 'admin-menu-container__nav-item--active' : ''
                }`}
                onClick={() => handleNavSelect(item.id)}
                type="button"
              >
                <span className="admin-menu-container__nav-icon">{item.icon}</span>
                <div className="admin-menu-container__nav-text">
                  <span className="admin-menu-container__nav-label">{item.label}</span>
                  <span className="admin-menu-container__nav-desc">{item.description}</span>
                </div>
                {activeNavId === item.id && (
                  <Check size={18} className="admin-menu-container__nav-check" />
                )}
              </button>
            ))}
          </div>
        </MobileFilterSheet>
      )}
    </div>
  );
}

export default AdminMenuContainer;
