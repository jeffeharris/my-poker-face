import { describe, it, expect, beforeEach, vi } from 'vitest';
import {
  clearTokens,
  configureTokenStorage,
  getAccessToken,
  getRefreshToken,
  hasNativeSession,
  isNativePlatform,
  loadTokens,
  refreshAccessToken,
  setRawFetch,
  setTokens,
  type TokenStorage,
} from '../nativeAuth';

function memoryStorage(initial: Partial<{ accessToken: string; refreshToken: string }> = {}) {
  const state = { ...initial };
  const adapter: TokenStorage = {
    load: vi.fn(async () => ({ ...state })),
    save: vi.fn(async (t) => {
      state.accessToken = t.accessToken;
      state.refreshToken = t.refreshToken;
    }),
    clear: vi.fn(async () => {
      delete state.accessToken;
      delete state.refreshToken;
    }),
  };
  return { adapter, state };
}

beforeEach(async () => {
  // Reset module state between tests.
  await clearTokens();
  configureTokenStorage(undefined as unknown as TokenStorage); // detach
  setRawFetch(globalThis.fetch);
  delete (window as unknown as { Capacitor?: unknown }).Capacitor;
});

describe('isNativePlatform', () => {
  it('is false on the web (no Capacitor global)', () => {
    expect(isNativePlatform()).toBe(false);
  });

  it('reflects Capacitor.isNativePlatform()', () => {
    (window as unknown as { Capacitor?: unknown }).Capacitor = {
      isNativePlatform: () => true,
    };
    expect(isNativePlatform()).toBe(true);
  });
});

describe('token store', () => {
  it('starts empty — no native session', () => {
    expect(getAccessToken()).toBeNull();
    expect(hasNativeSession()).toBe(false);
  });

  it('setTokens makes a native session and persists to storage', async () => {
    const { adapter, state } = memoryStorage();
    configureTokenStorage(adapter);

    await setTokens('access-1', 'refresh-1');

    expect(getAccessToken()).toBe('access-1');
    expect(getRefreshToken()).toBe('refresh-1');
    expect(hasNativeSession()).toBe(true);
    expect(adapter.save).toHaveBeenCalledOnce();
    expect(state.accessToken).toBe('access-1');
  });

  it('clearTokens wipes memory and storage', async () => {
    const { adapter } = memoryStorage();
    configureTokenStorage(adapter);
    await setTokens('a', 'r');

    await clearTokens();

    expect(getAccessToken()).toBeNull();
    expect(hasNativeSession()).toBe(false);
    expect(adapter.clear).toHaveBeenCalledOnce();
  });

  it('loadTokens hydrates from storage', async () => {
    const { adapter } = memoryStorage({ accessToken: 'a2', refreshToken: 'r2' });
    configureTokenStorage(adapter);

    await loadTokens();

    expect(getAccessToken()).toBe('a2');
    expect(getRefreshToken()).toBe('r2');
  });

  it('loadTokens tolerates a storage failure (treats as logged out)', async () => {
    configureTokenStorage({
      load: vi.fn(async () => {
        throw new Error('keychain unavailable');
      }),
      save: vi.fn(),
      clear: vi.fn(),
    });

    await loadTokens();
    expect(getAccessToken()).toBeNull();
  });
});

describe('refreshAccessToken', () => {
  it('returns false with no refresh token', async () => {
    expect(await refreshAccessToken()).toBe(false);
  });

  it('exchanges the refresh token and stores the new pair', async () => {
    await setTokens('old-access', 'old-refresh');
    const raw = vi.fn(
      async () =>
        new Response(JSON.stringify({ token: 'new-access', refresh_token: 'new-refresh' }), {
          status: 200,
        })
    );
    setRawFetch(raw as unknown as typeof fetch);

    const ok = await refreshAccessToken();

    expect(ok).toBe(true);
    expect(getAccessToken()).toBe('new-access');
    expect(getRefreshToken()).toBe('new-refresh');
    // Sends the *old* refresh token in the body.
    const body = JSON.parse((raw.mock.calls[0][1] as RequestInit).body as string);
    expect(body.refresh_token).toBe('old-refresh');
  });

  it('clears the session when the server rejects the refresh token', async () => {
    await setTokens('old-access', 'bad-refresh');
    setRawFetch((async () => new Response('{}', { status: 401 })) as unknown as typeof fetch);

    const ok = await refreshAccessToken();

    expect(ok).toBe(false);
    expect(hasNativeSession()).toBe(false);
  });

  it('keeps tokens on a network error (transient blip ≠ logout)', async () => {
    await setTokens('old-access', 'old-refresh');
    setRawFetch((async () => {
      throw new Error('offline');
    }) as unknown as typeof fetch);

    const ok = await refreshAccessToken();

    expect(ok).toBe(false);
    expect(getAccessToken()).toBe('old-access');
  });

  it('coalesces concurrent refreshes into a single request (single-flight)', async () => {
    await setTokens('old-access', 'old-refresh');
    const raw = vi.fn(
      async () =>
        new Response(JSON.stringify({ token: 'new-access', refresh_token: 'new-refresh' }), {
          status: 200,
        })
    );
    setRawFetch(raw as unknown as typeof fetch);

    const [a, b, c] = await Promise.all([
      refreshAccessToken(),
      refreshAccessToken(),
      refreshAccessToken(),
    ]);

    expect([a, b, c]).toEqual([true, true, true]);
    expect(raw).toHaveBeenCalledOnce();
  });
});
