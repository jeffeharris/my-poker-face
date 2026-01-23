import { useState, useEffect, useCallback } from 'react';
import { Shield, User, Users, RefreshCw, ShieldCheck, ShieldOff, Loader2 } from 'lucide-react';
import { config } from '../../config';
import { useAuth } from '../../hooks/useAuth';
import './AdminShared.css';
import './UserManagement.css';

// ============================================
// Types
// ============================================

interface UserStats {
  total_cost: number;
  hands_played: number;
  games_completed: number;
  last_active: string | null;
}

interface UserData {
  id: string;
  email: string | null;
  name: string;
  picture: string | null;
  is_guest: boolean;
  created_at: string;
  last_login: string | null;
  groups: string[];
  stats: UserStats;
}

interface AlertState {
  type: 'success' | 'error' | 'info';
  message: string;
}

// ============================================
// Helper Functions
// ============================================

function formatDate(dateStr: string | null): string {
  if (!dateStr) return 'Never';
  const date = new Date(dateStr);
  return date.toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  });
}

function formatCost(cost: number): string {
  if (cost === 0) return '$0';
  if (cost < 0.01) return '<$0.01';
  return `$${cost.toFixed(2)}`;
}

// ============================================
// Main Component
// ============================================

interface UserManagementProps {
  embedded?: boolean;
}

export function UserManagement({ embedded = false }: UserManagementProps) {
  const { user: currentUser } = useAuth();
  const [users, setUsers] = useState<UserData[]>([]);
  const [loading, setLoading] = useState(true);
  const [alert, setAlert] = useState<AlertState | null>(null);
  const [togglingUser, setTogglingUser] = useState<string | null>(null);

  // Clear alert after timeout
  useEffect(() => {
    if (alert) {
      const timer = setTimeout(() => setAlert(null), 5000);
      return () => clearTimeout(timer);
    }
  }, [alert]);

  // Fetch users
  const fetchUsers = useCallback(async () => {
    try {
      setLoading(true);
      const response = await fetch(`${config.API_URL}/api/admin/users`, {
        credentials: 'include',
      });

      if (!response.ok) {
        throw new Error('Failed to fetch users');
      }

      const data = await response.json();
      if (data.success) {
        setUsers(data.users);
      } else {
        throw new Error(data.error || 'Failed to fetch users');
      }
    } catch (error) {
      console.error('Error fetching users:', error);
      setAlert({
        type: 'error',
        message: error instanceof Error ? error.message : 'Failed to fetch users',
      });
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchUsers();
  }, [fetchUsers]);

  // Toggle admin status for a user
  const toggleAdmin = useCallback(async (userId: string, isCurrentlyAdmin: boolean) => {
    setTogglingUser(userId);
    try {
      const url = isCurrentlyAdmin
        ? `${config.API_URL}/api/admin/users/${userId}/groups/admin`
        : `${config.API_URL}/api/admin/users/${userId}/groups`;

      const response = await fetch(url, {
        method: isCurrentlyAdmin ? 'DELETE' : 'POST',
        credentials: 'include',
        headers: isCurrentlyAdmin ? undefined : { 'Content-Type': 'application/json' },
        body: isCurrentlyAdmin ? undefined : JSON.stringify({ group: 'admin' }),
      });

      const data = await response.json();

      if (!response.ok || !data.success) {
        throw new Error(data.error || 'Failed to update admin status');
      }

      // Update local state optimistically
      setUsers((prev) =>
        prev.map((u) =>
          u.id === userId
            ? {
                ...u,
                groups: isCurrentlyAdmin
                  ? u.groups.filter((g) => g !== 'admin')
                  : [...u.groups, 'admin'],
              }
            : u
        )
      );

      setAlert({
        type: 'success',
        message: isCurrentlyAdmin
          ? `Removed admin access from ${users.find((u) => u.id === userId)?.name}`
          : `Granted admin access to ${users.find((u) => u.id === userId)?.name}`,
      });
    } catch (error) {
      console.error('Error toggling admin status:', error);
      setAlert({
        type: 'error',
        message: error instanceof Error ? error.message : 'Failed to update admin status',
      });
    } finally {
      setTogglingUser(null);
    }
  }, [users]);

  // Render loading state
  if (loading) {
    return (
      <div className={`admin-panel ${embedded ? 'admin-panel--embedded' : ''}`}>
        <div className="admin-loading">
          <div className="admin-loading__spinner" />
          <span className="admin-loading__text">Loading users...</span>
        </div>
      </div>
    );
  }

  return (
    <div className={`admin-panel ${embedded ? 'admin-panel--embedded' : ''}`}>
      {/* Alert Toast */}
      {alert && (
        <div className="admin-toast-container">
          <div className={`admin-alert admin-alert--${alert.type}`}>
            <span className="admin-alert__content">{alert.message}</span>
            <button
              className="admin-alert__dismiss"
              onClick={() => setAlert(null)}
              aria-label="Dismiss"
            >
              &times;
            </button>
          </div>
        </div>
      )}

      {/* Header */}
      <div className="admin-header admin-header--row">
        <div className="admin-header__content">
          <h2 className="admin-header__title">User Management</h2>
          <p className="admin-header__subtitle">
            Manage user accounts and permissions
          </p>
        </div>
        <div className="admin-header__actions">
          <button
            className="admin-btn admin-btn--secondary"
            onClick={fetchUsers}
            disabled={loading}
          >
            <RefreshCw size={16} />
            Refresh
          </button>
        </div>
      </div>

      {/* Stats Summary */}
      <div className="um-stats">
        <div className="um-stat">
          <span className="um-stat__value">{users.length}</span>
          <span className="um-stat__label">Total Users</span>
        </div>
        <div className="um-stat">
          <span className="um-stat__value">
            {users.filter((u) => u.groups.includes('admin')).length}
          </span>
          <span className="um-stat__label">Admins</span>
        </div>
      </div>

      {/* Users Table */}
      <div className="admin-card">
        <div className="um-table-container">
          <table className="admin-table">
            <thead>
              <tr>
                <th>Name</th>
                <th>Groups</th>
                <th>Cost</th>
                <th>Hands</th>
                <th>Games</th>
                <th>Last Active</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {users.map((user) => {
                const isCurrentUser = user.id === currentUser?.id;
                const isAdmin = user.groups.includes('admin');
                const isToggling = togglingUser === user.id;
                // Guest users can't be made admin (except guest_jeff for dev)
                const canToggleAdmin = !user.is_guest || user.id === 'guest_jeff';

                return (
                  <tr key={user.id} className={isCurrentUser ? 'um-row--current' : ''}>
                    <td>
                      <div className="um-user">
                        {user.picture ? (
                          <img
                            src={user.picture}
                            alt={user.name}
                            className="um-user__avatar"
                          />
                        ) : (
                          <div className="um-user__avatar um-user__avatar--placeholder">
                            <User size={16} />
                          </div>
                        )}
                        <div className="um-user__info">
                          <span className="um-user__name">
                            {user.name}
                            {isCurrentUser && (
                              <span className="um-user__you">(you)</span>
                            )}
                            {user.is_guest && (
                              <span className="um-user__guest">Guest</span>
                            )}
                          </span>
                          {user.email && (
                            <span className="um-user__email">{user.email}</span>
                          )}
                        </div>
                      </div>
                    </td>
                    <td>
                      <div className="um-groups">
                        {isAdmin ? (
                          <span className="admin-badge admin-badge--warning">
                            <Shield size={12} />
                            admin
                          </span>
                        ) : user.groups.includes('user') ? (
                          <span className="admin-badge admin-badge--default">
                            user
                          </span>
                        ) : (
                          <span className="um-groups__placeholder">-</span>
                        )}
                      </div>
                    </td>
                    <td className="um-stat-cell">{formatCost(user.stats.total_cost)}</td>
                    <td className="um-stat-cell">{user.stats.hands_played}</td>
                    <td className="um-stat-cell">{user.stats.games_completed}</td>
                    <td className="um-date-cell">
                      {formatDate(user.stats.last_active)}
                    </td>
                    <td className="um-actions-cell">
                      {isCurrentUser ? (
                        <span className="um-actions__disabled">-</span>
                      ) : canToggleAdmin ? (
                        <button
                          className={`um-toggle-btn ${isAdmin ? 'um-toggle-btn--revoke' : 'um-toggle-btn--grant'}`}
                          onClick={() => toggleAdmin(user.id, isAdmin)}
                          disabled={isToggling}
                          title={isAdmin ? 'Revoke admin access' : 'Grant admin access'}
                        >
                          {isToggling ? (
                            <Loader2 size={14} className="um-toggle-btn__spinner" />
                          ) : isAdmin ? (
                            <ShieldOff size={14} />
                          ) : (
                            <ShieldCheck size={14} />
                          )}
                          <span>{isAdmin ? 'Revoke' : 'Grant'}</span>
                        </button>
                      ) : (
                        <span className="um-actions__disabled" title="Guest users cannot be admins">-</span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>

        {users.length === 0 && (
          <div className="admin-empty">
            <Users size={48} className="admin-empty__icon" />
            <h3 className="admin-empty__title">No users found</h3>
            <p className="admin-empty__description">
              Users will appear here once they sign in with Google.
            </p>
          </div>
        )}
      </div>
    </div>
  );
}

export default UserManagement;
