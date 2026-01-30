import { useState, useEffect, useRef, useCallback } from 'react';
import { ChevronDown, LogOut, Settings, Home } from 'lucide-react';
import './UserDropdown.css';

export interface UserDropdownProps {
  user: {
    name: string;
    is_guest: boolean;
    picture?: string;
    can_access_admin_tools?: boolean;
  };
  onLogout: () => void;
  onMainMenu?: () => void;
  onAdminTools?: () => void;
  handsPlayed?: number;
  handsLimit?: number;
  onOpen?: () => void;
}

/**
 * UserDropdown - Avatar + dropdown menu for user actions
 *
 * Features:
 * - Avatar circle with Google picture or initials
 * - Dropdown chevron indicator
 * - Click outside to close
 * - Escape key to close
 * - Smooth open/close animation
 */
export function UserDropdown({ user, onLogout, onMainMenu, onAdminTools, handsPlayed, handsLimit, onOpen }: UserDropdownProps) {
  const [isOpen, setIsOpen] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);

  // Get user's first initial for fallback avatar
  const initial = user.name.charAt(0).toUpperCase();

  // Close dropdown when clicking outside
  const handleClickOutside = useCallback((event: MouseEvent) => {
    if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
      setIsOpen(false);
    }
  }, []);

  // Close dropdown on Escape key
  const handleKeyDown = useCallback((event: KeyboardEvent) => {
    if (event.key === 'Escape') {
      setIsOpen(false);
    }
  }, []);

  useEffect(() => {
    if (isOpen) {
      document.addEventListener('mousedown', handleClickOutside);
      document.addEventListener('keydown', handleKeyDown);
    }

    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
      document.removeEventListener('keydown', handleKeyDown);
    };
  }, [isOpen, handleClickOutside, handleKeyDown]);

  const handleToggle = () => {
    setIsOpen((prev) => {
      if (!prev) onOpen?.();
      return !prev;
    });
  };

  const handleLogout = () => {
    setIsOpen(false);
    onLogout();
  };

  return (
    <div className="user-dropdown" ref={dropdownRef}>
      <button
        className="user-dropdown__trigger"
        onClick={handleToggle}
        aria-expanded={isOpen}
        aria-haspopup="true"
        aria-label="User menu"
      >
        {/* Avatar */}
        <div className="user-dropdown__avatar">
          {user.picture ? (
            <img
              src={user.picture}
              alt={user.name}
              className="user-dropdown__avatar-img"
              referrerPolicy="no-referrer"
            />
          ) : (
            <span className="user-dropdown__avatar-initial">{initial}</span>
          )}
        </div>

        {/* Chevron */}
        <ChevronDown
          size={16}
          className={`user-dropdown__chevron ${isOpen ? 'user-dropdown__chevron--open' : ''}`}
        />
      </button>

      {/* Dropdown Menu */}
      {isOpen && (
        <div className="user-dropdown__menu" role="menu">
          {/* User info header */}
          <div className="user-dropdown__header">
            <span className="user-dropdown__header-name">{user.name}</span>
            {user.is_guest && (
              <span className="user-dropdown__header-guest">Guest</span>
            )}
          </div>

          {user.is_guest && handsPlayed != null && handsLimit != null && (
            <div className="user-dropdown__hands-tracker">
              <div className="user-dropdown__hands-label">
                <span>Hands played</span>
                <span className="user-dropdown__hands-count">{handsPlayed}/{handsLimit}</span>
              </div>
              <div className="user-dropdown__hands-bar">
                <div
                  className="user-dropdown__hands-bar-fill"
                  style={{ width: `${Math.min((handsPlayed / handsLimit) * 100, 100)}%` }}
                />
              </div>
            </div>
          )}

          {onMainMenu && (
            <button
              className="user-dropdown__menu-item"
              onClick={() => { setIsOpen(false); onMainMenu(); }}
              role="menuitem"
            >
              <Home size={16} />
              <span>Main Menu</span>
            </button>
          )}

          {user.can_access_admin_tools && onAdminTools && (
            <button
              className="user-dropdown__menu-item"
              onClick={() => { setIsOpen(false); onAdminTools(); }}
              role="menuitem"
            >
              <Settings size={16} />
              <span>Admin Tools</span>
            </button>
          )}

          <button
            className="user-dropdown__menu-item user-dropdown__menu-item--logout"
            onClick={handleLogout}
            role="menuitem"
          >
            <LogOut size={16} />
            <span>Logout</span>
          </button>
        </div>
      )}
    </div>
  );
}
