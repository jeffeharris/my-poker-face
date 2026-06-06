import { useNavigate } from 'react-router-dom';
import { useAuth, hasPermission } from '../../hooks/useAuth';
import { useUsageStats } from '../../hooks/useUsageStats';
import { UserDropdown } from './UserDropdown';
import { rememberAdminOrigin } from '../admin/adminOrigin';
import './FloatingUserMenu.css';

export interface FloatingUserMenuProps {
  /** Where to return to when the user leaves admin (e.g. the current game
   *  path). Recorded as the admin origin when "Admin Tools" is opened. */
  returnTo: string;
}

/**
 * Floating avatar + dropdown for in-game views that don't have a full MenuBar
 * (the desktop poker table). Surfaces Settings / Main Menu / Admin Tools /
 * Logout. Entering admin from here records `returnTo` so the admin back-arrow
 * lands the user right back at this game.
 */
export function FloatingUserMenu({ returnTo }: FloatingUserMenuProps) {
  const navigate = useNavigate();
  const { user, logout } = useAuth();
  const { stats, refetch } = useUsageStats();

  if (!user) return null;

  const canAccessAdmin = hasPermission(user, 'can_access_admin_tools');

  const handleLogout = async () => {
    await logout();
    // Full page redirect to ensure all auth state is cleared (matches MenuBar).
    window.location.href = '/login';
  };

  return (
    <div className="floating-user-menu">
      <UserDropdown
        user={{ ...user, can_access_admin_tools: canAccessAdmin }}
        onLogout={handleLogout}
        onMainMenu={() => navigate('/menu')}
        onAdminTools={
          canAccessAdmin
            ? () => {
                rememberAdminOrigin(returnTo);
                navigate('/admin');
              }
            : undefined
        }
        handsPlayed={stats?.is_guest ? stats.hands_played : undefined}
        handsLimit={stats?.is_guest ? stats.hands_limit : undefined}
        onOpen={refetch}
      />
    </div>
  );
}
