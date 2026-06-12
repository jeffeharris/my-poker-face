import { config } from '../config';
import { getAccessToken, hasNativeSession, refreshAccessToken, setRawFetch } from './nativeAuth';

/**
 * Global API `fetch` wrapper: CSRF (web) + bearer auth (native).
 *
 * Double-submit-cookie CSRF (PRH-36): the backend issues a non-HttpOnly
 * `csrf_token` cookie and requires an `X-CSRF-Token` header equal to it on every
 * mutating `/api/*` request. Rather than thread that header through ~100
 * scattered `fetch()` call sites, we install one global `fetch` wrapper at app
 * bootstrap that attaches it automatically — and only to our own API's mutating
 * requests, never to third-party URLs (which would leak the token). Enforcement
 * is server-gated and same-origin-only (prod). In dev the SPA and API are
 * cross-origin, so `document.cookie` can't read the backend cookie and the
 * server leaves CSRF disabled — the wrapper finds no token and sends nothing
 * extra, which is harmless.
 *
 * Native bearer auth: in a Capacitor shell the same wrapper attaches
 * `Authorization: Bearer <access token>` to API requests and, on a 401,
 * refreshes the token once and retries. Both behaviors are gated on holding a
 * native token, so on the web this is a strict no-op and cookie auth is
 * untouched.
 */

const MUTATING_METHODS = new Set(['POST', 'PUT', 'PATCH', 'DELETE']);
const CSRF_COOKIE = 'csrf_token';
const CSRF_HEADER = 'X-CSRF-Token';

function readCookie(name: string): string | null {
  const match = document.cookie.match(new RegExp('(?:^|; )' + name + '=([^;]*)'));
  return match ? decodeURIComponent(match[1]) : null;
}

/** True only for our own API's requests (same-origin /api/* or the configured API_URL). */
function isApiRequest(url: string): boolean {
  try {
    if (config.API_URL && url.startsWith(config.API_URL)) {
      const rest = url.slice(config.API_URL.length);
      return rest.startsWith('/api/');
    }
    const u = new URL(url, window.location.origin);
    return u.origin === window.location.origin && u.pathname.startsWith('/api/');
  } catch {
    return false;
  }
}

/**
 * Monkeypatch `window.fetch` once to inject the CSRF header on mutating API
 * requests. Idempotent. Safe no-op when there's no token (dev / pre-bootstrap).
 */
// The refresh endpoint must never trigger its own refresh-retry (it's called
// via the raw fetch anyway, but guard defensively).
const REFRESH_PATH = '/api/auth/token/refresh';

export function installCsrfFetch(): void {
  if (typeof window === 'undefined') return;
  const w = window as Window & { __csrfFetchInstalled?: boolean };
  if (w.__csrfFetchInstalled) return;
  w.__csrfFetchInstalled = true;

  const originalFetch = window.fetch.bind(window);
  // Hand the pristine fetch to the native-auth module so token refresh bypasses
  // this wrapper (no stale bearer header, no 401-retry recursion).
  setRawFetch(originalFetch);

  window.fetch = async (input: RequestInfo | URL, init: RequestInit = {}): Promise<Response> => {
    const isRequestObj = typeof Request !== 'undefined' && input instanceof Request;
    const method = (init.method || (isRequestObj ? input.method : 'GET')).toUpperCase();
    const url = isRequestObj ? input.url : String(input);

    if (!isApiRequest(url)) {
      return originalFetch(input, init);
    }

    const headers = new Headers(init.headers || (isRequestObj ? input.headers : undefined));

    // CSRF (web/cookie auth): mutating requests echo the double-submit token.
    if (MUTATING_METHODS.has(method)) {
      const csrf = readCookie(CSRF_COOKIE);
      if (csrf && !headers.has(CSRF_HEADER)) headers.set(CSRF_HEADER, csrf);
    }

    // Native bearer auth: attach the access token (no-op on web — no token).
    const access = getAccessToken();
    if (access && !headers.has('Authorization')) {
      headers.set('Authorization', `Bearer ${access}`);
    }

    init = { ...init, headers, credentials: init.credentials ?? 'include' };
    let res = await originalFetch(input, init);

    // Native only: on a 401, refresh once and retry the request with the fresh
    // token. Skip Request objects (their body may already be consumed) and the
    // refresh endpoint itself.
    if (res.status === 401 && hasNativeSession() && !isRequestObj && !url.includes(REFRESH_PATH)) {
      const refreshed = await refreshAccessToken();
      if (refreshed) {
        const retryHeaders = new Headers(headers);
        retryHeaders.set('Authorization', `Bearer ${getAccessToken()}`);
        res = await originalFetch(input, { ...init, headers: retryHeaders });
      }
    }

    return res;
  };
}
