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
 * Common API mocks shared across all navigation tests.
 */
async function setupCommonMocks(page: import('@playwright/test').Page) {
  // Auth - current user
  await page.route('**/api/auth/me', route =>
    route.fulfill({
      json: {
        user: {
          id: 'user-456',
          name: 'TestPlayer',
          is_guest: false,
          created_at: '2024-01-01',
          permissions: ['play', 'custom_game', 'themed_game']
        }
      }
    })
  );

  // Saved games
  await page.route('**/api/games', route =>
    route.fulfill({ json: { games: [] } })
  );

  // Career stats
  await page.route('**/api/career-stats*', route =>
    route.fulfill({ json: { games_played: 5, games_won: 2, win_rate: 0.4, total_knockouts: 3 } })
  );

  // Usage stats
  await page.route('**/api/usage-stats*', route =>
    route.fulfill({ json: { hands_played: 3, hands_limit: 20 } })
  );

  // Personalities
  await page.route('**/api/personalities', route =>
    route.fulfill({ json: { personalities: [] } })
  );

  // Health
  await page.route('**/health', route =>
    route.fulfill({ json: { status: 'ok' } })
  );

  // New game
  await page.route('**/api/new-game', route =>
    route.fulfill({ json: { game_id: 'test-game-123' } })
  );

  // Game state
  const fullGameState = {
    ...gameStateFixture,
    player_options: gameStateFixture.players[0].player_options,
    highest_bet: gameStateFixture.betting_context.highest_bet,
    min_raise: gameStateFixture.betting_context.min_raise_to,
  };
  await page.route('**/api/game-state/test-game-123', route =>
    route.fulfill({ json: fullGameState })
  );

  // Player action
  await page.route('**/api/game/*/action', route =>
    route.fulfill({ json: { success: true } })
  );

  // Chat
  await page.route('**/api/game/*/chat', route =>
    route.fulfill({ json: { success: true } })
  );

  // Post-round chat
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

  // Socket.IO - provide handshake + connect so the game page loads properly
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
}

/**
 * Set localStorage for an authenticated registered user.
 */
async function setUserAuth(page: import('@playwright/test').Page) {
  await page.evaluate(() => {
    localStorage.setItem('currentUser', JSON.stringify({
      id: 'user-456',
      name: 'TestPlayer',
      is_guest: false,
      created_at: '2024-01-01',
      permissions: ['play', 'custom_game', 'themed_game']
    }));
  });
}

test.describe('PW-17: Mobile navigation — back button returns to menu', () => {

  test('from game page, back button in MenuBar navigates to /menu', async ({ page }) => {
    await setupCommonMocks(page);

    // Navigate to game page (two-step: set localStorage then navigate)
    await page.goto('/game/test-game-123', { waitUntil: 'commit' });
    await setUserAuth(page);
    await page.goto('/game/test-game-123');

    // Wait for the mobile poker table to load
    const table = page.locator('.mobile-poker-table');
    await expect(table).toBeVisible({ timeout: 10000 });

    // Click the back button in the MenuBar
    const backButton = page.locator('.menu-bar__back');
    await expect(backButton).toBeVisible({ timeout: 5000 });
    await backButton.click();

    // Should navigate to /menu
    await page.waitForURL('**/menu', { timeout: 5000 });
    await expect(page).toHaveURL(/\/menu/);
  });

  test('from career stats, back button navigates to /menu', async ({ page }) => {
    await setupCommonMocks(page);

    // Navigate to stats page
    await page.goto('/stats', { waitUntil: 'commit' });
    await setUserAuth(page);
    await page.goto('/stats');

    // Wait for the stats page MenuBar to be visible
    const menuBar = page.locator('.menu-bar');
    await expect(menuBar).toBeVisible({ timeout: 10000 });

    // Click the back button
    const backButton = page.locator('.menu-bar__back');
    await expect(backButton).toBeVisible({ timeout: 5000 });
    await backButton.click();

    // Should navigate to /menu
    await page.waitForURL('**/menu', { timeout: 5000 });
    await expect(page).toHaveURL(/\/menu/);
  });

  test('from custom game config, back button navigates to /menu', async ({ page }) => {
    await setupCommonMocks(page);

    // Navigate to custom game config page
    await page.goto('/game/new/custom', { waitUntil: 'commit' });
    await setUserAuth(page);
    await page.goto('/game/new/custom');

    // Wait for the page to load - look for MenuBar or wizard content
    const menuBar = page.locator('.menu-bar');
    await expect(menuBar).toBeVisible({ timeout: 10000 });

    // Click the back button
    const backButton = page.locator('.menu-bar__back');
    await expect(backButton).toBeVisible({ timeout: 5000 });
    await backButton.click();

    // Should navigate to /menu
    await page.waitForURL('**/menu', { timeout: 5000 });
    await expect(page).toHaveURL(/\/menu/);
  });

  test('browser back button works from stats to menu', async ({ page }) => {
    await setupCommonMocks(page);

    // First go to /menu
    await page.goto('/menu', { waitUntil: 'commit' });
    await setUserAuth(page);
    await page.goto('/menu');

    // Wait for menu to load
    await expect(page.locator('.quick-play-section')).toBeVisible({ timeout: 10000 });

    // Navigate to stats using a click (to create browser history)
    // The menu has a stats option - but it may be easier to navigate directly
    // and check browser back. Let's navigate via URL to create history entry.
    await page.goto('/stats');

    // Wait for stats page
    const menuBar = page.locator('.menu-bar');
    await expect(menuBar).toBeVisible({ timeout: 10000 });

    // Use browser back button
    await page.goBack();

    // Should return to /menu
    await page.waitForURL('**/menu', { timeout: 5000 });
    await expect(page).toHaveURL(/\/menu/);
  });

});
