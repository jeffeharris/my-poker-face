import { Page, expect } from '@playwright/test';
import { readFileSync } from 'fs';
import { dirname, join } from 'path';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

const gameStateFixture = JSON.parse(
  readFileSync(join(__dirname, './fixtures/game-state.json'), 'utf-8')
);

/**
 * Backend URL for test helper endpoints.
 * In Docker compose, BACKEND_URL points directly to the backend service.
 * Locally, the backend runs on port 5000.
 */
const BACKEND_URL = process.env.BACKEND_URL || 'http://localhost:5000';

// ─── Dual-mode: mock (default) vs real backend ───

export const TEST_MODE = process.env.TEST_MODE || 'mock';
export const isRealMode = () => TEST_MODE === 'real';

// Pending socket events for real-mode delivery after navigation
let _pendingSocketEvents: Array<[string, unknown]> = [];
let _pendingGameId = '';

// ─── Shared game-state builder ───

/**
 * Build a full game state from the fixture with the given player_options on the human player.
 * Adjusts top-level fields the frontend GameState type expects.
 */
export function buildGameState(
  playerOptions: string[] = ['fold', 'call', 'raise'],
  extraOverrides: Record<string, unknown> = {}
) {
  const players = (gameStateFixture.players as Record<string, unknown>[]).map((p, i) => {
    if (i === 0) return { ...p, player_options: playerOptions };
    return p;
  });
  return {
    ...gameStateFixture,
    players,
    player_options: playerOptions,
    highest_bet: gameStateFixture.betting_context.highest_bet,
    min_raise: gameStateFixture.betting_context.min_raise_to,
    ...extraOverrides,
  };
}

// ─── Mock-based helpers (still needed for tests requiring precise control) ───

/**
 * Mock all common API routes so tests don't need a live backend.
 */
export async function mockAPIRoutes(page: Page, overrides: Record<string, unknown> = {}) {
  const gameState = { ...gameStateFixture, ...overrides };

  // Auth - guest login
  await page.route('**/api/auth/guest', route =>
    route.fulfill({ json: { id: 'guest-123', name: 'TestPlayer', is_guest: true, permissions: ['play'] } })
  );

  // Auth - current user
  await page.route('**/api/auth/me', route =>
    route.fulfill({ json: { id: 'guest-123', name: 'TestPlayer', is_guest: true, permissions: ['play'] } })
  );

  // New game
  await page.route('**/api/new-game', route =>
    route.fulfill({ json: { game_id: 'test-game-123' } })
  );

  // Game state
  await page.route('**/api/game/test-game-123/state', route =>
    route.fulfill({ json: gameState })
  );

  // Player action
  await page.route('**/api/game/*/action', route =>
    route.fulfill({ json: { success: true } })
  );

  // Saved games
  await page.route('**/api/games', route =>
    route.fulfill({ json: { games: [] } })
  );

  // Personalities
  await page.route('**/api/personalities', route =>
    route.fulfill({ json: { personalities: [] } })
  );

  // Usage stats
  await page.route('**/api/usage-stats*', route =>
    route.fulfill({ json: { hands_played: 3, hands_limit: 20 } })
  );

  // Career stats
  await page.route('**/api/career-stats*', route =>
    route.fulfill({ json: { games_played: 5, games_won: 2, win_rate: 0.4, total_knockouts: 3 } })
  );

  // Chat
  await page.route('**/api/game/*/chat', route =>
    route.fulfill({ json: { success: true } })
  );

  // Post-round chat suggestions
  await page.route('**/api/game/*/post-round-chat*', route =>
    route.fulfill({
      json: {
        suggestions: [
          { text: "Nice hand!", tone: "humble" },
          { text: "Got lucky there.", tone: "humble" }
        ]
      }
    })
  );

  // Health
  await page.route('**/health', route =>
    route.fulfill({ json: { status: 'ok' } })
  );
}

/**
 * Set localStorage for an authenticated guest user.
 */
export async function loginAsGuest(page: Page, name = 'TestPlayer') {
  await page.evaluate((playerName) => {
    localStorage.setItem('user', JSON.stringify({
      id: 'guest-123',
      name: playerName,
      is_guest: true,
      permissions: ['play']
    }));
  }, name);
}

/**
 * Set localStorage for an authenticated registered user.
 */
export async function loginAsUser(page: Page, name = 'TestPlayer') {
  await page.evaluate((playerName) => {
    localStorage.setItem('user', JSON.stringify({
      id: 'user-456',
      name: playerName,
      is_guest: false,
      permissions: ['play', 'custom_game', 'themed_game']
    }));
  }, name);
}

// ─── Real-backend helpers (for use with ENABLE_TEST_ROUTES backend) ───

/**
 * Load a game state snapshot into the backend via the test endpoint.
 * Requires ENABLE_TEST_ROUTES=true on the backend.
 */
export async function loadGameSnapshot(
  page: Page,
  gameId: string,
  snapshot: Record<string, unknown>
): Promise<void> {
  const response = await page.request.post(`${BACKEND_URL}/api/test/set-game-state`, {
    data: { game_id: gameId, snapshot },
  });
  if (!response.ok()) {
    throw new Error(`Failed to load snapshot: ${response.status()} ${await response.text()}`);
  }
}

/**
 * Emit a Socket.IO event to a game room via the test endpoint.
 * Requires ENABLE_TEST_ROUTES=true on the backend.
 */
export async function emitSocketEvent(
  page: Page,
  gameId: string,
  event: string,
  data: Record<string, unknown>
): Promise<void> {
  const response = await page.request.post(`${BACKEND_URL}/api/test/emit-event/${gameId}`, {
    data: { event, data },
  });
  if (!response.ok()) {
    throw new Error(`Failed to emit event: ${response.status()} ${await response.text()}`);
  }
}

/**
 * Reset all in-memory game state on the backend.
 * Requires ENABLE_TEST_ROUTES=true on the backend.
 */
export async function resetTestState(page: Page): Promise<void> {
  const response = await page.request.post(`${BACKEND_URL}/api/test/reset`);
  if (!response.ok()) {
    throw new Error(`Failed to reset state: ${response.status()} ${await response.text()}`);
  }
}

/**
 * Login as a real guest via the backend API and set localStorage.
 */
export async function loginAsTestGuest(page: Page, name = 'TestPlayer'): Promise<void> {
  const response = await page.request.post(`${BACKEND_URL}/api/auth/guest`, {
    data: { name },
  });
  if (response.ok()) {
    const data = await response.json();
    const user = data.user || data;
    await page.evaluate((u) => {
      localStorage.setItem('currentUser', JSON.stringify(u));
    }, user);
  } else {
    // Fallback: set localStorage directly for guest
    await page.evaluate((playerName) => {
      localStorage.setItem('currentUser', JSON.stringify({
        id: 'guest-123',
        name: playerName,
        is_guest: true,
        created_at: new Date().toISOString(),
        permissions: ['play']
      }));
    }, name);
  }
}

// ─── Common mock setup for game-page tests ───

/**
 * Standard mock setup used by most game-page tests.
 * Intercepts useAuth module, mocks common API routes, and mocks Socket.IO.
 *
 * Returns a helper to control Socket.IO events.
 */
export async function mockGamePageRoutes(
  page: Page,
  opts: {
    isGuest?: boolean;
    gameState?: Record<string, unknown>;
    gameId?: string;
    socketEvents?: Array<[string, unknown]>;
    socketConnected?: boolean;
    usageStats?: { hands_played: number; hands_limit: number; [key: string]: unknown };
  } = {}
) {
  const isGuest = opts.isGuest !== false;
  const gameId = opts.gameId || 'test-game-123';
  const gameState = opts.gameState || gameStateFixture;
  const socketEvents = opts.socketEvents || [];
  const socketConnected = opts.socketConnected !== false;
  const usageStats = opts.usageStats || { hands_played: 3, hands_limit: 20 };

  // ── Real-backend mode: skip most mocks, let requests hit real backend ──
  if (TEST_MODE === 'real') {
    // Only mock LLM endpoints that require API keys
    await page.route('**/api/game/*/post-round-chat*', route =>
      route.fulfill({
        json: {
          suggestions: [
            { text: 'Nice hand!', tone: 'humble' },
            { text: 'Got lucky there.', tone: 'humble' },
          ],
        },
      })
    );
    await page.route('**/api/game/*/chat-suggestions*', route =>
      route.fulfill({
        json: {
          suggestions: [
            { text: 'Nice play!', category: 'compliment' },
            { text: 'You got me there!', category: 'concession' },
          ],
        },
      })
    );

    // If socketConnected is explicitly false, block socket.io to simulate disconnect
    if (!socketConnected) {
      await page.route('**/socket.io/**', route => route.abort());
    }

    // If gameState provided, load it into the real backend via test endpoint
    if (opts.gameState) {
      await loadGameSnapshot(page, gameId, gameState as Record<string, unknown>);
    }

    // Store pending socket events for delivery after navigation
    _pendingSocketEvents = socketEvents;
    _pendingGameId = gameId;
    return;
  }

  // ── Mock mode (default): intercept all routes ──

  // Intercept useAuth to disable dev-mode guest bypass
  await page.route('**/@fs/**useAuth**', async route => {
    const response = await route.fetch();
    let body = await response.text();
    body = body.replace(
      /import\.meta\.env\.VITE_FORCE_GUEST\s*!==\s*['"]true['"]/,
      'false'
    );
    await route.fulfill({ response, body });
  });
  await page.route('**/src/hooks/useAuth**', async route => {
    const response = await route.fetch();
    let body = await response.text();
    body = body.replace(
      /import\.meta\.env\.VITE_FORCE_GUEST\s*!==\s*['"]true['"]/,
      'false'
    );
    await route.fulfill({ response, body });
  });

  // Mock auth
  await page.route('**/api/auth/me', route =>
    route.fulfill({
      json: {
        user: {
          id: isGuest ? 'guest-123' : 'user-456',
          name: 'TestPlayer',
          is_guest: isGuest,
          created_at: '2024-01-01',
          permissions: isGuest ? ['play'] : ['play', 'custom_game', 'themed_game']
        }
      }
    })
  );

  // Mock common API endpoints
  await page.route('**/api/games', route =>
    route.fulfill({ json: { games: [] } })
  );
  await page.route('**/api/career-stats*', route =>
    route.fulfill({ json: { games_played: 5, games_won: 2, win_rate: 0.4, total_knockouts: 3 } })
  );
  await page.route('**/api/usage-stats*', route =>
    route.fulfill({ json: usageStats })
  );
  await page.route('**/api/personalities', route =>
    route.fulfill({ json: { personalities: [] } })
  );
  await page.route('**/health', route =>
    route.fulfill({ json: { status: 'ok' } })
  );
  await page.route('**/api/new-game', route =>
    route.fulfill({ json: { game_id: gameId } })
  );
  await page.route(`**/api/game-state/${gameId}`, route =>
    route.fulfill({ json: gameState })
  );
  await page.route('**/api/game/*/action', route =>
    route.fulfill({ json: { success: true } })
  );
  await page.route('**/api/game/*/chat', route =>
    route.fulfill({ json: { success: true } })
  );
  await page.route('**/api/game/*/post-round-chat*', route =>
    route.fulfill({
      json: {
        suggestions: [
          { text: 'Nice hand!', tone: 'humble' },
          { text: 'Got lucky there.', tone: 'humble' }
        ]
      }
    })
  );
  await page.route('**/api/game/*/chat-suggestions*', route =>
    route.fulfill({
      json: {
        suggestions: [
          { text: 'Nice play!', category: 'compliment' },
          { text: 'You got me there!', category: 'concession' }
        ]
      }
    })
  );
  await page.route('**/api/end_game/**', route =>
    route.fulfill({ json: { success: true } })
  );

  // Socket.IO mock
  let pollCount = 0;
  await page.route('**/socket.io/**', route => {
    const url = route.request().url();
    if (url.includes('transport=polling') && route.request().method() === 'GET') {
      if (!url.includes('sid=')) {
        // Engine.IO handshake
        route.fulfill({
          contentType: 'text/plain',
          body: '0{"sid":"fake-sid","upgrades":[],"pingInterval":25000,"pingTimeout":20000}'
        });
      } else {
        pollCount++;
        if (pollCount === 1 && socketConnected) {
          // Socket.IO connect ack
          route.fulfill({
            contentType: 'text/plain',
            body: '40{"sid":"fake-socket-sid"}'
          });
        } else if (pollCount > 1 && socketConnected && socketEvents.length > 0) {
          const eventIdx = pollCount - 2;
          if (eventIdx < socketEvents.length) {
            const [eventName, eventData] = socketEvents[eventIdx];
            const payload = JSON.stringify([eventName, eventData]);
            route.fulfill({
              contentType: 'text/plain',
              body: `42${payload}`
            });
          } else {
            route.fulfill({ contentType: 'text/plain', body: '6' });
          }
        } else if (!socketConnected) {
          // Don't send CONNECT — keeps socket in disconnected state
          route.fulfill({ contentType: 'text/plain', body: '6' });
        } else {
          route.fulfill({ contentType: 'text/plain', body: '6' });
        }
      }
    } else if (route.request().method() === 'POST') {
      route.fulfill({ contentType: 'text/plain', body: 'ok' });
    } else {
      route.fulfill({ body: '' });
    }
  });
}

/**
 * Navigate to game page with localStorage set for the user.
 */
export async function navigateToGamePage(
  page: Page,
  opts: {
    isGuest?: boolean;
    gameId?: string;
  } = {}
) {
  const isGuest = opts.isGuest !== false;
  const gameId = opts.gameId || 'test-game-123';

  if (TEST_MODE === 'real') {
    // Real mode: use real login via backend API
    await page.goto('/menu', { waitUntil: 'commit' });
    await loginAsTestGuest(page);
    await page.goto(`/game/${gameId}`);
    await expect(page.locator('.mobile-poker-table')).toBeVisible({ timeout: 15000 });

    // Deliver any pending socket events via backend API
    if (_pendingSocketEvents.length > 0) {
      await page.waitForTimeout(500); // ensure socket connection established
      for (const [event, data] of _pendingSocketEvents) {
        await emitSocketEvent(page, _pendingGameId, event, data as Record<string, unknown>);
      }
      _pendingSocketEvents = [];
    }
    return;
  }

  // Mock mode: set localStorage directly
  await page.goto('/menu', { waitUntil: 'commit' });
  await page.evaluate((guest) => {
    localStorage.setItem('currentUser', JSON.stringify({
      id: guest ? 'guest-123' : 'user-456',
      name: 'TestPlayer',
      is_guest: guest,
      created_at: '2024-01-01',
      permissions: guest ? ['play'] : ['play', 'custom_game', 'themed_game']
    }));
  }, isGuest);
  await page.goto(`/game/${gameId}`);
  await expect(page.locator('.mobile-poker-table')).toBeVisible({ timeout: 10000 });
}

// ─── Common mock setup for menu-page tests ───

/**
 * Standard mock setup for tests that only need menu/auth mocks (no game state or Socket.IO events).
 * Used by: landing, login, menu, navigation, guest-limit, offline-detection, custom-game-wizard tests.
 */
export async function mockMenuPageRoutes(
  page: Page,
  opts: {
    isGuest?: boolean;
    authenticated?: boolean;
    handsPlayed?: number;
    handsLimit?: number;
    handsLimitReached?: boolean;
    personalities?: unknown;
    userModels?: Record<string, unknown>;
    includeAvatar?: boolean;
    usageStats?: Record<string, unknown>;
  } = {}
) {
  const isGuest = opts.isGuest !== false;
  const authenticated = opts.authenticated !== false;
  const handsPlayed = opts.handsPlayed ?? 3;
  const handsLimit = opts.handsLimit ?? 20;

  // ── Real-backend mode: skip most mocks, let requests hit real backend ──
  if (TEST_MODE === 'real') {
    // Only mock LLM endpoints
    await page.route('**/api/game/*/post-round-chat*', route =>
      route.fulfill({ json: { suggestions: [] } })
    );
    await page.route('**/api/game/*/chat-suggestions*', route =>
      route.fulfill({ json: { suggestions: [] } })
    );

    // If hands limit override needed (guest-limit test), mock just usage-stats
    if (opts.handsLimitReached || opts.usageStats) {
      await page.route('**/api/usage-stats*', route =>
        route.fulfill({
          json: opts.usageStats || {
            hands_played: handsPlayed,
            hands_limit: handsLimit,
            hands_limit_reached: true,
            max_opponents: 3,
            max_active_games: 1,
            is_guest: isGuest,
          },
        })
      );
    }
    return;
  }

  // ── Mock mode (default): intercept all routes ──

  // Intercept useAuth to disable dev-mode guest bypass
  await page.route('**/@fs/**useAuth**', async route => {
    const response = await route.fetch();
    let body = await response.text();
    body = body.replace(
      /import\.meta\.env\.VITE_FORCE_GUEST\s*!==\s*['"]true['"]/,
      'false'
    );
    await route.fulfill({ response, body });
  });
  await page.route('**/src/hooks/useAuth**', async route => {
    const response = await route.fetch();
    let body = await response.text();
    body = body.replace(
      /import\.meta\.env\.VITE_FORCE_GUEST\s*!==\s*['"]true['"]/,
      'false'
    );
    await route.fulfill({ response, body });
  });

  // Mock auth
  if (authenticated) {
    await page.route('**/api/auth/me', route =>
      route.fulfill({
        json: {
          user: {
            id: isGuest ? 'guest-123' : 'user-456',
            name: 'TestPlayer',
            is_guest: isGuest,
            created_at: '2024-01-01',
            permissions: isGuest ? ['play'] : ['play', 'custom_game', 'themed_game']
          }
        }
      })
    );
  } else {
    await page.route('**/api/auth/me', route =>
      route.fulfill({ status: 401, json: { error: 'Not authenticated' } })
    );
  }

  // Mock common API endpoints
  await page.route('**/api/games', route =>
    route.fulfill({ json: { games: [] } })
  );
  await page.route('**/api/career-stats*', route =>
    route.fulfill({ json: { games_played: 5, games_won: 2, win_rate: 0.4, total_knockouts: 3 } })
  );
  await page.route('**/api/usage-stats*', route =>
    route.fulfill({
      json: opts.usageStats || {
        hands_played: handsPlayed,
        hands_limit: handsLimit,
        hands_limit_reached: opts.handsLimitReached ?? (handsPlayed >= handsLimit),
        max_opponents: 3,
        max_active_games: 1,
        is_guest: isGuest,
      }
    })
  );
  await page.route('**/api/personalities', route =>
    route.fulfill({ json: opts.personalities !== undefined ? opts.personalities : { personalities: [] } })
  );
  await page.route('**/health', route =>
    route.fulfill({ json: { status: 'ok' } })
  );

  // Optional: user-models for custom game wizard
  if (opts.userModels) {
    await page.route('**/api/user-models', route =>
      route.fulfill({ json: opts.userModels })
    );
  }

  // Optional: avatar endpoint for custom-game-step2
  if (opts.includeAvatar) {
    await page.route('**/api/avatar/**', route =>
      route.fulfill({
        contentType: 'image/png',
        body: Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==', 'base64'),
      })
    );
  }

  // Mock socket.io (no game events needed)
  await page.route('**/socket.io/**', route => {
    const url = route.request().url();
    if (url.includes('transport=polling') && route.request().method() === 'GET') {
      if (!url.includes('sid=')) {
        route.fulfill({
          contentType: 'text/plain',
          body: '0{"sid":"fake-sid","upgrades":[],"pingInterval":25000,"pingTimeout":20000}'
        });
      } else {
        route.fulfill({ contentType: 'text/plain', body: '6' });
      }
    } else if (route.request().method() === 'POST') {
      route.fulfill({ contentType: 'text/plain', body: 'ok' });
    } else {
      route.fulfill({ body: '' });
    }
  });
}

/**
 * Navigate to the menu page with localStorage set for the user.
 */
export async function navigateToMenuPage(
  page: Page,
  opts: {
    isGuest?: boolean;
    path?: string;
  } = {}
) {
  const isGuest = opts.isGuest !== false;
  const path = opts.path || '/menu';

  if (TEST_MODE === 'real') {
    // Real mode: use real login via backend API
    await page.goto(path, { waitUntil: 'commit' });
    await loginAsTestGuest(page);
    await page.goto(path);
    return;
  }

  // Mock mode: set localStorage directly
  await page.goto(path, { waitUntil: 'commit' });
  await page.evaluate((guest) => {
    localStorage.setItem('currentUser', JSON.stringify({
      id: guest ? 'guest-123' : 'user-456',
      name: 'TestPlayer',
      is_guest: guest,
      created_at: '2024-01-01',
      permissions: guest ? ['play'] : ['play', 'custom_game', 'themed_game']
    }));
  }, isGuest);
  await page.goto(path);
}
