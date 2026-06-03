import { config } from '../config';

/**
 * Double-submit-cookie CSRF support (PRH-36).
 *
 * The backend issues a non-HttpOnly `csrf_token` cookie and requires an
 * `X-CSRF-Token` header equal to it on every mutating `/api/*` request. Rather
 * than thread that header through ~100 scattered `fetch()` call sites, we
 * install one global `fetch` wrapper at app bootstrap that attaches it
 * automatically — and only to our own API's mutating requests, never to
 * third-party URLs (which would leak the token).
 *
 * Enforcement is server-gated and same-origin-only (prod). In dev the SPA and
 * API are cross-origin, so `document.cookie` can't read the backend cookie and
 * the server leaves CSRF disabled — the wrapper simply finds no token and sends
 * nothing extra, which is harmless.
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
export function installCsrfFetch(): void {
  if (typeof window === 'undefined') return;
  const w = window as Window & { __csrfFetchInstalled?: boolean };
  if (w.__csrfFetchInstalled) return;
  w.__csrfFetchInstalled = true;

  const originalFetch = window.fetch.bind(window);

  window.fetch = (input: RequestInfo | URL, init: RequestInit = {}): Promise<Response> => {
    const isRequestObj = typeof Request !== 'undefined' && input instanceof Request;
    const method = (init.method || (isRequestObj ? input.method : 'GET')).toUpperCase();
    const url = isRequestObj ? input.url : String(input);

    if (MUTATING_METHODS.has(method) && isApiRequest(url)) {
      const token = readCookie(CSRF_COOKIE);
      if (token) {
        const headers = new Headers(init.headers || (isRequestObj ? input.headers : undefined));
        if (!headers.has(CSRF_HEADER)) headers.set(CSRF_HEADER, token);
        init = { ...init, headers, credentials: init.credentials ?? 'include' };
      }
    }

    return originalFetch(input, init);
  };
}
