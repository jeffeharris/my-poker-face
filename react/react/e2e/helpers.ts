import { Page } from '@playwright/test';
import gameStateFixture from './fixtures/game-state.json';

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
