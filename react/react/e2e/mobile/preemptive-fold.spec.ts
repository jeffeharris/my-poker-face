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
 * Build a game state where it's an AI player's turn (not the human's).
 * current_player_idx=1 means Batman is thinking. Human has no player_options.
 */
function buildWaitingGameState(extraOverrides: Record<string, unknown> = {}) {
  const players = gameStateFixture.players.map((p: Record<string, unknown>, i: number) => {
    if (i === 0) return { ...p, player_options: [] }; // Human has no options - not their turn
    return p;
  });
  return {
    ...gameStateFixture,
    current_player_idx: 1, // Batman's turn
    players,
    player_options: [],
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

test.describe('PW-08: Preemptive fold while waiting for opponent', () => {

  test('shows waiting text when it is not the human player\'s turn', async ({ page }) => {
    const gameState = buildWaitingGameState();
    await setupGamePage(page, gameState);

    // Waiting text should be visible with opponent name
    const waitingText = page.locator('.waiting-text');
    await expect(waitingText).toBeVisible({ timeout: 5000 });
    await expect(waitingText).toContainText(/thinking|waiting/i);
  });

  test('preemptive fold button is available during wait', async ({ page }) => {
    const gameState = buildWaitingGameState();
    await setupGamePage(page, gameState);

    // Preemptive button should be visible
    const preemptiveBtn = page.locator('.action-btn.preemptive-btn');
    await expect(preemptiveBtn).toBeVisible({ timeout: 5000 });

    // Should show Chk/Fold label
    await expect(preemptiveBtn).toContainText(/chk\/fold/i);
  });

  test('tapping preemptive fold queues the action and shows queued state', async ({ page }) => {
    const gameState = buildWaitingGameState();
    await setupGamePage(page, gameState);

    const preemptiveBtn = page.locator('.action-btn.preemptive-btn');
    await expect(preemptiveBtn).toBeVisible({ timeout: 5000 });

    // Tap the preemptive fold button
    await preemptiveBtn.click();

    // Button should now show "Queued" state
    await expect(preemptiveBtn).toHaveClass(/queued/);
    await expect(preemptiveBtn).toContainText(/queued/i);
  });

  test('tapping queued preemptive fold again dequeues the action', async ({ page }) => {
    const gameState = buildWaitingGameState();
    await setupGamePage(page, gameState);

    const preemptiveBtn = page.locator('.action-btn.preemptive-btn');
    await expect(preemptiveBtn).toBeVisible({ timeout: 5000 });

    // Queue the action
    await preemptiveBtn.click();
    await expect(preemptiveBtn).toHaveClass(/queued/);

    // Dequeue the action
    await preemptiveBtn.click();
    await expect(preemptiveBtn).not.toHaveClass(/queued/);
    await expect(preemptiveBtn).toContainText(/chk\/fold/i);
  });

  test('chat button remains available during waiting state', async ({ page }) => {
    const gameState = buildWaitingGameState();
    await setupGamePage(page, gameState);

    // Chat button should still be visible
    const chatBtn = page.locator('.action-btn.chat-btn');
    await expect(chatBtn).toBeVisible({ timeout: 5000 });
  });

  test('standard action buttons are not visible when waiting for opponent', async ({ page }) => {
    const gameState = buildWaitingGameState();
    await setupGamePage(page, gameState);

    // Wait for the table to be ready
    await expect(page.locator('.waiting-text')).toBeVisible({ timeout: 5000 });

    // Fold, Call, Raise buttons should NOT be visible
    await expect(page.locator('.action-btn.fold-btn')).not.toBeVisible();
    await expect(page.locator('.action-btn.call-btn')).not.toBeVisible();
    await expect(page.locator('.action-btn.raise-btn')).not.toBeVisible();
  });

});
