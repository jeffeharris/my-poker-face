import { ChevronLeft } from 'lucide-react';
import { useAuth, hasPermission } from '../../hooks/useAuth';
import { UserDropdown } from './UserDropdown';
import './MenuBar.css';

export interface MenuBarProps {
  /** Handler for back button. Shows back arrow if provided */
  onBack?: () => void;
  /** Optional center title (for sub-screens) */
  title?: string;
  /** Show "My Poker Face" branding (default: false) */
  showBrand?: boolean;
  /** Show user badge + logout (default: true) */
  showUserInfo?: boolean;
  /** Handler for navigating to main menu */
  onMainMenu?: () => void;
  /** Handler for navigating to admin tools */
  onAdminTools?: () => void;
  /** Additional class name */
  className?: string;
}

/**
 * MenuBar - Unified top bar for all menu screens
 *
 * Features:
 * - Fixed position at top with safe area insets
 * - Left: Back button (optional) or empty space
 * - Center: Brand name "My Poker Face" (main menu) OR screen title (sub-screens)
 * - Right: User info (name, guest badge, logout)
 * - Subtle background blur + border
 */
export function MenuBar({
  onBack,
  title,
  showBrand = false,
  showUserInfo = true,
  onMainMenu,
  onAdminTools,
  className = '',
}: MenuBarProps) {
  const { user, logout: authLogout } = useAuth();
  const canAccessAdmin = user ? hasPermission(user, 'can_access_admin_tools') : false;

  const handleLogout = async () => {
    await authLogout();
    // Full page redirect to ensure all auth state is cleared
    window.location.href = '/login';
  };

  return (
    <header className={`menu-bar ${className}`.trim()}>
      {/* Left section: Back button or spacer */}
      <div className="menu-bar__left">
        {onBack ? (
          <button
            className="menu-bar__back"
            onClick={onBack}
            aria-label="Go back"
          >
            <span className="menu-bar__back-icon">
              <ChevronLeft size={28} strokeWidth={2.5} />
            </span>
            <span className="menu-bar__back-label">Back</span>
          </button>
        ) : (
          <div className="menu-bar__spacer" />
        )}
      </div>

      {/* Center section: Brand or title */}
      <div className="menu-bar__center">
        {showBrand ? (
          <span className="menu-bar__brand">My Poker Face</span>
        ) : title ? (
          <span className="menu-bar__title">{title}</span>
        ) : null}
      </div>

      {/* Right section: User info */}
      <div className="menu-bar__right">
        {showUserInfo && user && (
          <UserDropdown
            user={{ ...user, can_access_admin_tools: canAccessAdmin }}
            onLogout={handleLogout}
            onMainMenu={onMainMenu}
            onAdminTools={canAccessAdmin ? onAdminTools : undefined}
          />
        )}
      </div>
    </header>
  );
}
