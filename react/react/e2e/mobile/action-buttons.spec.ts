import { test, expect } from '@playwright/test';
import { readFileSync } from 'fs';
import { dirname, join } from 'path';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

const gameStateFixture = JSON.parse(
  readFileSync(join(__dirname, '../fixtures/game-state.json'), 'utf-8')
);

/**
 * Build a full game state with the given player_options on the human player.
 * Adjusts top-level fields the frontend expects.
 */
function buildGameState(playerOptions: string[], extraOverrides: Record<string, unknown> = {}) {
  const players = gameStateFixture.players.map((p: Record<string, unknown>, i: number) => {
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

/**
 * Set up all mocks and navigate to the game page with a given game state.
 */
async function setupGamePage(page: import('@playwright/test').Page, gameState: Record<string, unknown>) {
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
          id: 'guest-123',
          name: 'TestPlayer',
          is_guest: true,
          created_at: '2024-01-01',
          permissions: ['play']
        }
      }
    })
  );

  // Mock saved games
  await page.route('**/api/games', route =>
    route.fulfill({ json: { games: [] } })
  );

  // Mock career stats
  await page.route('**/api/career-stats*', route =>
    route.fulfill({ json: { games_played: 5, games_won: 2, win_rate: 0.4, total_knockouts: 3 } })
  );

  // Mock usage stats
  await page.route('**/api/usage-stats*', route =>
    route.fulfill({ json: { hands_played: 3, hands_limit: 20 } })
  );

  // Mock personalities
  await page.route('**/api/personalities', route =>
    route.fulfill({ json: { personalities: [] } })
  );

  // Mock health
  await page.route('**/health', route =>
    route.fulfill({ json: { status: 'ok' } })
  );

  // Mock new-game
  await page.route('**/api/new-game', route =>
    route.fulfill({ json: { game_id: 'test-game-123' } })
  );

  // Mock game state
  await page.route('**/api/game-state/test-game-123', route =>
    route.fulfill({ json: gameState })
  );

  // Mock player action
  await page.route('**/api/game/*/action', route =>
    route.fulfill({ json: { success: true } })
  );

  // Mock chat
  await page.route('**/api/game/*/chat', route =>
    route.fulfill({ json: { success: true } })
  );

  // Mock post-round chat
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

  // Mock Socket.IO polling transport
  let socketConnectSent = false;
  await page.route('**/socket.io/**', route => {
    const url = route.request().url();
    if (url.includes('transport=polling') && route.request().method() === 'GET') {
      if (!url.includes('sid=')) {
        route.fulfill({
          contentType: 'text/plain',
          body: '0{"sid":"fake-sid","upgrades":[],"pingInterval":25000,"pingTimeout":20000}'
        });
      } else if (!socketConnectSent) {
        socketConnectSent = true;
        route.fulfill({
          contentType: 'text/plain',
          body: '40{"sid":"fake-socket-sid"}'
        });
      } else {
        route.fulfill({
          contentType: 'text/plain',
          body: '6'
        });
      }
    } else if (route.request().method() === 'POST') {
      route.fulfill({ contentType: 'text/plain', body: 'ok' });
    } else {
      route.fulfill({ body: '' });
    }
  });

  // Navigate to menu, set localStorage, then navigate to game
  await page.goto('/menu', { waitUntil: 'commit' });
  await page.evaluate(() => {
    localStorage.setItem('currentUser', JSON.stringify({
      id: 'guest-123',
      name: 'TestPlayer',
      is_guest: true,
      created_at: '2024-01-01',
      permissions: ['play']
    }));
  });

  // Navigate directly to game page
  await page.goto('/game/test-game-123');

  // Wait for mobile poker table to render
  await expect(page.locator('.mobile-poker-table')).toBeVisible({ timeout: 10000 });
}

test.describe('PW-06: Mobile action buttons display correct options per game state', () => {

  test('pre-flop with fold/call/raise shows Fold, Call, Raise buttons', async ({ page }) => {
    const gameState = buildGameState(['fold', 'call', 'raise']);
    await setupGamePage(page, gameState);

    const actionButtons = page.locator('.mobile-action-buttons');
    await expect(actionButtons).toBeVisible();

    // Fold button visible
    await expect(page.locator('.action-btn.fold-btn')).toBeVisible();

    // Call button visible with amount text
    const callBtn = page.locator('.action-btn.call-btn');
    await expect(callBtn).toBeVisible();

    // Raise button visible
    await expect(page.locator('.action-btn.raise-btn')).toBeVisible();

    // Check button should NOT be visible
    await expect(page.locator('.action-btn.check-btn')).not.toBeVisible();
  });

  test('big blind option: fold/check/raise shows Fold, Check, Raise buttons', async ({ page }) => {
    const gameState = buildGameState(['fold', 'check', 'raise']);
    await setupGamePage(page, gameState);

    const actionButtons = page.locator('.mobile-action-buttons');
    await expect(actionButtons).toBeVisible();

    // Fold button visible
    await expect(page.locator('.action-btn.fold-btn')).toBeVisible();

    // Check button visible
    await expect(page.locator('.action-btn.check-btn')).toBeVisible();

    // Raise button visible
    await expect(page.locator('.action-btn.raise-btn')).toBeVisible();

    // Call button should NOT be visible
    await expect(page.locator('.action-btn.call-btn')).not.toBeVisible();
  });

  test('only all-in available: fold/all_in shows Fold and All-In buttons', async ({ page }) => {
    const gameState = buildGameState(['fold', 'all_in']);
    await setupGamePage(page, gameState);

    const actionButtons = page.locator('.mobile-action-buttons');
    await expect(actionButtons).toBeVisible();

    // Fold button visible
    await expect(page.locator('.action-btn.fold-btn')).toBeVisible();

    // All-In button visible
    await expect(page.locator('.action-btn.allin-btn')).toBeVisible();

    // Raise button should NOT be visible
    await expect(page.locator('.action-btn.raise-btn')).not.toBeVisible();
  });

  test('chat button is always present when onQuickChat provided', async ({ page }) => {
    const gameState = buildGameState(['fold', 'call', 'raise']);
    await setupGamePage(page, gameState);

    const actionButtons = page.locator('.mobile-action-buttons');
    await expect(actionButtons).toBeVisible();

    // Chat button visible
    await expect(page.locator('.action-btn.chat-btn')).toBeVisible();
  });

});
