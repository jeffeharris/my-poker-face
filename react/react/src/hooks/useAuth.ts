import { useState, useEffect, useCallback } from 'react';
import { config } from '../config';

interface User {
  id: string;
  name: string;
  is_guest: boolean;
  created_at: string;
  permissions?: string[];
  email?: string;    // Available for Google users
  picture?: string;  // Google profile picture URL
}

/**
 * Check if a user has a specific permission.
 * @param user - The user object (can be null)
 * @param permission - The permission name to check
 * @returns true if the user has the permission, false otherwise
 */
export function hasPermission(user: User | null, permission: string): boolean {
  return user?.permissions?.includes(permission) ?? false;
}

interface AuthState {
  user: User | null;
  isLoading: boolean;
  isAuthenticated: boolean;
}

export function useAuth() {
  const [authState, setAuthState] = useState<AuthState>({
    user: null,
    isLoading: true,
    isAuthenticated: false,
  });

  const checkAuth = useCallback(async () => {
    try {
      // Use localStorage for initial state while loading, but always verify with backend
      const storedUser = localStorage.getItem('currentUser');
      const authToken = localStorage.getItem('authToken');

      // Set initial state from localStorage (optimistic)
      if (storedUser) {
        const user = JSON.parse(storedUser);
        setAuthState({
          user,
          isLoading: true, // Still loading - will verify with backend
          isAuthenticated: true,
        });
      }

      // Always check with backend to get fresh permissions
      const headers: HeadersInit = {
        'Content-Type': 'application/json',
      };

      if (authToken) {
        headers['Authorization'] = `Bearer ${authToken}`;
      }

      const response = await fetch(`${config.API_URL}/api/auth/me`, {
        credentials: 'include',
        headers,
      });

      if (response.ok) {
        const data = await response.json();
        if (data.user) {
          setAuthState({
            user: data.user,
            isLoading: false,
            isAuthenticated: true,
          });
          // Update localStorage with fresh data including permissions
          localStorage.setItem('currentUser', JSON.stringify(data.user));
        } else {
          setAuthState({
            user: null,
            isLoading: false,
            isAuthenticated: false,
          });
          localStorage.removeItem('currentUser');
        }
      } else {
        setAuthState({
          user: null,
          isLoading: false,
          isAuthenticated: false,
        });
        localStorage.removeItem('currentUser');
      }
    } catch (error) {
      console.error('Auth check failed:', error);
      // On error, err on the side of security: treat user as unauthenticated
      // This prevents stale permissions from being used when backend is unreachable
      localStorage.removeItem('currentUser');
      setAuthState({
        user: null,
        isLoading: false,
        isAuthenticated: false,
      });
    }
  }, []);

  // Check for existing session on mount
  useEffect(() => {
    checkAuth();
  }, [checkAuth]);

  // Handle OAuth callback parameters
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const authResult = params.get('auth');
    const errorMessage = params.get('message');

    if (authResult) {
      // Clear the URL parameters
      window.history.replaceState({}, '', window.location.pathname);

      if (authResult === 'success') {
        // OAuth was successful - refresh auth state
        checkAuth();
      } else if (authResult === 'error') {
        console.error('OAuth error:', errorMessage);
        // Could show a toast/notification here
      }
    }
  }, [checkAuth]);

  const login = useCallback(async (name: string, isGuest: boolean = true) => {
    try {
      const response = await fetch(`${config.API_URL}/api/auth/login`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        credentials: 'include',
        body: JSON.stringify({
          guest: isGuest,
          name,
        }),
      });

      const data = await response.json();

      if (data.success) {
        setAuthState({
          user: data.user,
          isLoading: false,
          isAuthenticated: true,
        });

        // Store in localStorage
        if (data.token) {
          localStorage.setItem('authToken', data.token);
        }
        localStorage.setItem('currentUser', JSON.stringify(data.user));

        return { success: true };
      } else {
        return { success: false, error: data.error };
      }
    } catch (error) {
      console.error('Login failed:', error);
      return { success: false, error: 'Connection error' };
    }
  }, []);

  const logout = useCallback(async () => {
    try {
      await fetch(`${config.API_URL}/api/auth/logout`, {
        method: 'POST',
        credentials: 'include',
      });
    } catch (error) {
      console.error('Logout failed:', error);
    }

    // Clear local state regardless
    setAuthState({
      user: null,
      isLoading: false,
      isAuthenticated: false,
    });
    localStorage.removeItem('currentUser');
    localStorage.removeItem('authToken');
  }, []);

  return {
    user: authState.user,
    isLoading: authState.isLoading,
    isAuthenticated: authState.isAuthenticated,
    login,
    logout,
    checkAuth,
  };
}