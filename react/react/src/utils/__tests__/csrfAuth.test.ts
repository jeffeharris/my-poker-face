import { describe, it, expect, beforeEach, vi } from 'vitest';
import { config } from '../../config';
import { installCsrfFetch } from '../csrf';
import { clearTokens, setTokens } from '../nativeAuth';

const API = `${config.API_URL}/api/foo`;

/** Re-arm the idempotent installer over a given base fetch and return the
 *  wrapped window.fetch plus the underlying mock. */
function install(baseFetch: typeof fetch) {
  (window as unknown as { __csrfFetchInstalled?: boolean }).__csrfFetchInstalled = false;
  window.fetch = baseFetch;
  installCsrfFetch();
  return window.fetch;
}

function headersOf(call: unknown[]): Headers {
  const init = call[1] as RequestInit;
  return new Headers(init.headers);
}

beforeEach(async () => {
  await clearTokens();
  // Clear the csrf cookie.
  document.cookie = 'csrf_token=; expires=Thu, 01 Jan 1970 00:00:00 GMT';
});

describe('global fetch wrapper — CSRF (web)', () => {
  it('attaches X-CSRF-Token to mutating API requests when the cookie is set', async () => {
    document.cookie = 'csrf_token=tok-123';
    const base = vi.fn(async () => new Response('{}', { status: 200 }));
    const wrapped = install(base as unknown as typeof fetch);

    await wrapped(API, { method: 'POST' });

    expect(headersOf(base.mock.calls[0]).get('X-CSRF-Token')).toBe('tok-123');
  });

  it('does not attach Authorization on the web (no native token)', async () => {
    document.cookie = 'csrf_token=tok-123';
    const base = vi.fn(async () => new Response('{}', { status: 200 }));
    const wrapped = install(base as unknown as typeof fetch);

    await wrapped(API, { method: 'POST' });

    expect(headersOf(base.mock.calls[0]).has('Authorization')).toBe(false);
  });

  it('leaves non-API (third-party) requests untouched', async () => {
    document.cookie = 'csrf_token=tok-123';
    const base = vi.fn(async () => new Response('{}', { status: 200 }));
    const wrapped = install(base as unknown as typeof fetch);

    await wrapped('https://example.com/track', { method: 'POST' });

    expect(headersOf(base.mock.calls[0]).has('X-CSRF-Token')).toBe(false);
  });
});

describe('global fetch wrapper — bearer (native)', () => {
  it('attaches Authorization: Bearer to API requests when a token is held', async () => {
    await setTokens('access-1', 'refresh-1');
    const base = vi.fn(async () => new Response('{}', { status: 200 }));
    const wrapped = install(base as unknown as typeof fetch);

    await wrapped(API, { method: 'GET' });

    expect(headersOf(base.mock.calls[0]).get('Authorization')).toBe('Bearer access-1');
  });

  it('refreshes once on a 401 and retries with the new token', async () => {
    await setTokens('stale', 'refresh-1');

    let dataCalls = 0;
    const base = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes('/api/auth/token/refresh')) {
        return new Response(JSON.stringify({ token: 'fresh', refresh_token: 'refresh-2' }), {
          status: 200,
        });
      }
      dataCalls += 1;
      // First hit 401 (stale token), second hit (retry) 200.
      const auth = new Headers(init?.headers).get('Authorization');
      if (dataCalls === 1) {
        expect(auth).toBe('Bearer stale');
        return new Response('{}', { status: 401 });
      }
      expect(auth).toBe('Bearer fresh');
      return new Response('{"ok":true}', { status: 200 });
    });

    const wrapped = install(base as unknown as typeof fetch);
    const res = await wrapped(API, { method: 'GET' });

    expect(res.status).toBe(200);
    expect(dataCalls).toBe(2);
  });

  it('does not retry when there is no native session (web 401 passes through)', async () => {
    const base = vi.fn(async () => new Response('{}', { status: 401 }));
    const wrapped = install(base as unknown as typeof fetch);

    const res = await wrapped(API, { method: 'GET' });

    expect(res.status).toBe(401);
    expect(base).toHaveBeenCalledOnce();
  });
});
