import { config } from '../config';

/**
 * Native (mobile) auth transport.
 *
 * On the web the app authenticates with HttpOnly session cookies and this whole
 * module stays inert: no token is ever set, so every helper is a no-op and the
 * cookie flow is untouched (PRH-37 — no JS-readable token on web).
 *
 * In a native shell (Capacitor) the WebView can't rely on cross-origin cookies
 * for the API and can't set headers on the WebSocket upgrade, so we authenticate
 * with a JWT pair obtained from `POST /api/auth/google/native`:
 *   - the access token is attached as `Authorization: Bearer` to API requests
 *     (see `utils/csrf.ts`) and passed in the Socket.IO `auth` payload
 *     (see `utils/socket.ts`);
 *   - on a 401 it is refreshed once via `POST /api/auth/token/refresh`.
 *
 * Token persistence is pluggable via {@link configureTokenStorage}. Until a
 * secure-storage adapter (iOS Keychain / Android Keystore) is wired in the
 * native bootstrap, tokens live in memory only and a cold start requires
 * re-authentication.
 */

export interface TokenPair {
  accessToken: string;
  refreshToken: string;
}

export interface TokenStorage {
  load(): Promise<Partial<TokenPair>>;
  save(tokens: TokenPair): Promise<void>;
  clear(): Promise<void>;
}

// In-memory source of truth for the current session.
let accessToken: string | null = null;
let refreshToken: string | null = null;
let storage: TokenStorage | null = null;

// The original (un-wrapped) fetch, captured before our wrappers install, so a
// token refresh never recurses through the bearer/401-retry wrapper.
let rawFetch: typeof fetch = (...args: Parameters<typeof fetch>) => globalThis.fetch(...args);

interface CapacitorLike {
  isNativePlatform?: () => boolean;
}

function capacitor(): CapacitorLike | undefined {
  if (typeof window === 'undefined') return undefined;
  return (window as unknown as { Capacitor?: CapacitorLike }).Capacitor;
}

/** True when running inside a Capacitor native shell (iOS/Android). */
export function isNativePlatform(): boolean {
  return capacitor()?.isNativePlatform?.() ?? false;
}

/** Capture the pristine fetch (called by the fetch wrapper installer). */
export function setRawFetch(fn: typeof fetch): void {
  rawFetch = fn;
}

/** Install a persistence adapter (e.g. Keychain-backed) for the token pair. */
export function configureTokenStorage(adapter: TokenStorage): void {
  storage = adapter;
}

/** Hydrate the in-memory tokens from persistent storage, if configured. */
export async function loadTokens(): Promise<void> {
  if (!storage) return;
  try {
    const t = await storage.load();
    accessToken = t.accessToken ?? null;
    refreshToken = t.refreshToken ?? null;
  } catch {
    // A storage read failure should not block app boot — treat as logged out.
    accessToken = null;
    refreshToken = null;
  }
}

export function getAccessToken(): string | null {
  return accessToken;
}

export function getRefreshToken(): string | null {
  return refreshToken;
}

/** True once a native access token is held — gates all bearer/refresh logic. */
export function hasNativeSession(): boolean {
  return accessToken != null;
}

export async function setTokens(token: string, refresh: string = ''): Promise<void> {
  // refresh defaults to '' for the native guest flow, which issues a long-lived
  // access token and no refresh token (the refresh endpoint serves real accounts
  // only). refreshAccessToken() no-ops on a falsy refresh token, so a guest 401
  // surfaces without logging them out.
  accessToken = token;
  refreshToken = refresh;
  if (storage) {
    try {
      await storage.save({ accessToken: token, refreshToken: refresh });
    } catch {
      // Persistence is best-effort; the in-memory copy still authenticates this
      // session even if the secure store rejected the write.
    }
  }
}

export async function clearTokens(): Promise<void> {
  accessToken = null;
  refreshToken = null;
  if (storage) {
    try {
      await storage.clear();
    } catch {
      // ignore — in-memory state is already cleared
    }
  }
}

// Single-flight guard so a burst of concurrent 401s triggers exactly one
// refresh round-trip; all callers await the same result.
let refreshInFlight: Promise<boolean> | null = null;

/**
 * Refresh the access token using the stored refresh token. Returns true on
 * success (a new token pair is now in place), false otherwise (and clears the
 * session so the caller can route to login). Concurrent calls share one
 * in-flight request.
 */
export function refreshAccessToken(): Promise<boolean> {
  if (!refreshToken) return Promise.resolve(false);
  if (!refreshInFlight) {
    refreshInFlight = doRefresh().finally(() => {
      refreshInFlight = null;
    });
  }
  return refreshInFlight;
}

async function doRefresh(): Promise<boolean> {
  const rt = refreshToken;
  if (!rt) return false;
  try {
    // rawFetch bypasses our own wrapper so we never send the stale access token
    // or re-enter the 401-retry path while refreshing.
    const res = await rawFetch(`${config.API_URL}/api/auth/token/refresh`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh_token: rt }),
    });
    if (!res.ok) {
      await clearTokens();
      return false;
    }
    const data = await res.json();
    if (data?.token && data?.refresh_token) {
      await setTokens(data.token, data.refresh_token);
      return true;
    }
    await clearTokens();
    return false;
  } catch {
    // Network failure: keep the (possibly still-valid) tokens; the caller just
    // sees the original 401. Don't clear — a transient blip shouldn't log out.
    return false;
  }
}
