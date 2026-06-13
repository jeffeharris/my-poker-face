import {
  createContext,
  useContext,
  useState,
  useEffect,
  useCallback,
  useRef,
  type ReactNode,
} from 'react';
import { config } from '../config';
import { logger } from '../utils/logger';
import { clearTokens, isNativePlatform, loadTokens, setTokens } from '../utils/nativeAuth';

interface User {
  id: string;
  name: string;
  is_guest: boolean;
  created_at: string;
  permissions?: string[];
  email?: string; // Available for Google users
  picture?: string; // Google profile picture URL
  avatar_url?: string; // Custom profile avatar (relative path; prefer over picture)
  bio?: string; // AI-visible self-description set on /profile
}

/**
 * Check if a user has a specific permission.
 * @param user - The user object (can be null)
 * @param permission - The permission name to check
 * @returns true if the user has the permission, false otherwise
 */
// eslint-disable-next-line react-refresh/only-export-components
export function hasPermission(user: User | null, permission: string): boolean {
  return user?.permissions?.includes(permission) ?? false;
}

interface AuthState {
  user: User | null;
  isLoading: boolean;
  isAuthenticated: boolean;
}

interface AuthContextValue extends AuthState {
  login: (name: string, isGuest?: boolean) => Promise<{ success: boolean; error?: string }>;
  loginWithGoogleNative: (idToken: string) => Promise<{ success: boolean; error?: string }>;
  logout: () => Promise<void>;
  checkAuth: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [authState, setAuthState] = useState<AuthState>({
    user: null,
    isLoading: true,
    isAuthenticated: false,
  });

  // Prevent duplicate requests from React strict mode double-invoking effects
  const checkInProgressRef = useRef(false);

  const checkAuth = useCallback(async () => {
    if (checkInProgressRef.current) return;
    checkInProgressRef.current = true;
    try {
      // Native: hydrate the bearer tokens from secure storage before the verify
      // call so the global fetch wrapper can attach Authorization. No-op on web.
      await loadTokens();

      // Use localStorage for initial state while loading, but always verify with backend
      const storedUser = localStorage.getItem('currentUser');

      // Set initial state from localStorage (optimistic)
      if (storedUser) {
        const user = JSON.parse(storedUser);
        setAuthState({
          user,
          isLoading: true, // Still loading - will verify with backend
          isAuthenticated: true,
        });
      }

      // Always check with backend to get fresh permissions. Auth rides the
      // HttpOnly session / guest cookies (credentials: 'include') — we no
      // longer store or send a bearer token in browser-readable storage
      // (PRH-37: shrinks the XSS blast radius).
      const response = await fetch(`${config.API_URL}/api/auth/me`, {
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
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
      logger.error('Auth check failed:', error);
      // On error, err on the side of security: treat user as unauthenticated
      // This prevents stale permissions from being used when backend is unreachable
      localStorage.removeItem('currentUser');
      setAuthState({
        user: null,
        isLoading: false,
        isAuthenticated: false,
      });
    } finally {
      checkInProgressRef.current = false;
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
        logger.error('OAuth error:', errorMessage);
        // Could show a toast/notification here
      }
    }
  }, [checkAuth]);

  const login = useCallback(async (name: string, isGuest: boolean = true) => {
    try {
      // Native guest sign-in needs a bearer JWT — cookies don't bridge to the
      // native WebView (cross-origin), so the web cookie flow leaves a native guest
      // unauthenticated. Use the native guest endpoint and stash the token (same as
      // loginWithGoogleNative). Falls through to the cookie flow on any failure, so
      // there's no regression if the backend endpoint isn't deployed yet.
      if (isGuest && isNativePlatform()) {
        try {
          const storedGuestId = localStorage.getItem('mpf_guest_id') || undefined;
          const res = await fetch(`${config.API_URL}/api/auth/guest/native`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name, guest_id: storedGuestId }),
          });
          const native = await res.json();
          if (native.success && native.token) {
            await setTokens(native.token); // long-lived; no refresh token for guests
            if (native.guest_id) localStorage.setItem('mpf_guest_id', native.guest_id);
            setAuthState({ user: native.user, isLoading: false, isAuthenticated: true });
            localStorage.setItem('currentUser', JSON.stringify(native.user));
            return { success: true };
          }
        } catch {
          // Endpoint unavailable (e.g. not yet deployed) — fall through to cookies.
        }
      }

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

        // Cache the user for optimistic load only — the session/guest cookies
        // (set by the backend on this response) carry auth, not a stored token
        // (PRH-37).
        localStorage.setItem('currentUser', JSON.stringify(data.user));

        return { success: true };
      } else {
        return { success: false, error: data.error };
      }
    } catch (error) {
      logger.error('Login failed:', error);
      return { success: false, error: 'Connection error' };
    }
  }, []);

  const loginWithGoogleNative = useCallback(async (idToken: string) => {
    // Native (Capacitor) sign-in: the platform Google SDK supplies an ID token,
    // which we exchange for our own JWT pair. Tokens are stashed via setTokens
    // (the global fetch wrapper + socket factory pick them up automatically).
    try {
      const response = await fetch(`${config.API_URL}/api/auth/google/native`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id_token: idToken }),
      });

      const data = await response.json();

      if (data.success && data.token && data.refresh_token) {
        await setTokens(data.token, data.refresh_token);
        setAuthState({
          user: data.user,
          isLoading: false,
          isAuthenticated: true,
        });
        localStorage.setItem('currentUser', JSON.stringify(data.user));
        return { success: true };
      }
      return { success: false, error: data.error || 'Sign-in failed' };
    } catch (error) {
      logger.error('Native Google login failed:', error);
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
      logger.error('Logout failed:', error);
    }

    // Drop native bearer tokens (no-op on web).
    await clearTokens();

    // Clear local state regardless
    setAuthState({
      user: null,
      isLoading: false,
      isAuthenticated: false,
    });
    localStorage.removeItem('currentUser');
    // Clear any legacy bearer token left by an older client build (PRH-37).
    localStorage.removeItem('authToken');
  }, []);

  // In dev mode, treat guests as non-guests so all features are accessible.
  // Set VITE_FORCE_GUEST=true in .env to test guest restrictions locally.
  const bypassGuest = import.meta.env.DEV && import.meta.env.VITE_FORCE_GUEST !== 'true';
  const user =
    authState.user && bypassGuest && authState.user.is_guest
      ? { ...authState.user, is_guest: false as const }
      : authState.user;

  const value: AuthContextValue = {
    user,
    isLoading: authState.isLoading,
    isAuthenticated: authState.isAuthenticated,
    login,
    loginWithGoogleNative,
    logout,
    checkAuth,
  };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

// eslint-disable-next-line react-refresh/only-export-components
export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return context;
}
