import './UserBadge.css';

export interface UserBadgeProps {
  /** User's display name */
  name: string;
  /** Whether user is a guest */
  isGuest?: boolean;
  /** Logout handler */
  onLogout: () => void;
  /** Additional class name */
  className?: string;
}

/**
 * User info badge with logout button.
 *
 * Displays user name (with guest indicator) and logout action.
 * Position using CSS on the parent or with className.
 */
export function UserBadge({
  name,
  isGuest = false,
  onLogout,
  className = '',
}: UserBadgeProps) {
  return (
    <div className={`user-badge ${className}`.trim()}>
      <span className="user-badge__name">
        {name}
        {isGuest && <span className="user-badge__guest-tag">(Guest)</span>}
      </span>
      <button className="user-badge__logout" onClick={onLogout}>
        Logout
      </button>
    </div>
  );
}
