import { useState, useEffect, useCallback } from 'react';
import { config } from '../config';

interface User {
  id: string;
  name: string;
  is_guest: boolean;
  created_at: string;
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

  // Check for existing session on mount
  useEffect(() => {
    checkAuth();
  }, []);

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
  }, []);

  const checkAuth = async () => {
    try {
      // Check localStorage first
      const storedUser = localStorage.getItem('currentUser');
      const authToken = localStorage.getItem('authToken');
      
      if (storedUser) {
        const user = JSON.parse(storedUser);
        setAuthState({
          user,
          isLoading: false,
          isAuthenticated: true,
        });
        return;
      }

      // Check with backend
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
          localStorage.setItem('currentUser', JSON.stringify(data.user));
        } else {
          setAuthState({
            user: null,
            isLoading: false,
            isAuthenticated: false,
          });
        }
      } else {
        setAuthState({
          user: null,
          isLoading: false,
          isAuthenticated: false,
        });
      }
    } catch (error) {
      console.error('Auth check failed:', error);
      setAuthState({
        user: null,
        isLoading: false,
        isAuthenticated: false,
      });
    }
  };

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