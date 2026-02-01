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
 * Set up mocks and navigate to a game page.
 * The `socketConnected` parameter controls whether the Socket.IO CONNECT
 * packet is sent, which determines if usePokerGame sets isConnected=true.
 */
async function setupGamePage(page: import('@playwright/test').Page, socketConnected: boolean) {
  // Mock auth/me
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

  // Mock game state endpoint
  const fullGameState = {
    ...gameStateFixture,
    player_options: gameStateFixture.players[0].player_options,
    highest_bet: gameStateFixture.betting_context.highest_bet,
    min_raise: gameStateFixture.betting_context.min_raise_to,
  };
  await page.route('**/api/game-state/test-game-123', route =>
    route.fulfill({ json: fullGameState })
  );

  // Mock player action
  await page.route('**/api/game/*/action', route =>
    route.fulfill({ json: { success: true } })
  );

  // Mock chat endpoints
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
  // When socketConnected=false, we do the handshake but never send the CONNECT packet,
  // so the client's socket stays in a "not connected" state.
  let socketConnectSent = false;
  await page.route('**/socket.io/**', route => {
    const url = route.request().url();
    if (url.includes('transport=polling') && route.request().method() === 'GET') {
      if (!url.includes('sid=')) {
        // Engine.IO handshake — always respond so polling starts
        route.fulfill({
          contentType: 'text/plain',
          body: '0{"sid":"fake-sid","upgrades":[],"pingInterval":25000,"pingTimeout":20000}'
        });
      } else if (socketConnected && !socketConnectSent) {
        // Send Socket.IO CONNECT packet only if socketConnected=true
        socketConnectSent = true;
        route.fulfill({
          contentType: 'text/plain',
          body: '40{"sid":"fake-socket-sid"}'
        });
      } else {
        // Keep-alive / noop — never sends CONNECT when socketConnected=false
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

  // Set localStorage and navigate directly to game page
  await page.goto('/game/test-game-123', { waitUntil: 'commit' });
  await page.evaluate(() => {
    localStorage.setItem('currentUser', JSON.stringify({
      id: 'guest-123',
      name: 'TestPlayer',
      is_guest: true,
      created_at: '2024-01-01',
      permissions: ['play']
    }));
  });
  await page.goto('/game/test-game-123');
}

test.describe('PW-16: Reconnecting overlay appears when socket drops', () => {

  test('reconnecting overlay visible when socket is disconnected during game', async ({ page }) => {
    // Set up game page with socket NOT connected
    await setupGamePage(page, false);

    // The mobile poker table should render (game state is loaded via HTTP)
    const table = page.locator('.mobile-poker-table');
    await expect(table).toBeVisible({ timeout: 10000 });

    // The reconnecting overlay should appear because isConnected=false and gameState exists
    const overlay = page.locator('.mobile-reconnecting-overlay');
    await expect(overlay).toBeVisible({ timeout: 5000 });

    // Verify it shows "Reconnecting..." text
    await expect(overlay).toContainText('Reconnecting');
  });

  test('reconnecting overlay has spinner element', async ({ page }) => {
    await setupGamePage(page, false);

    const table = page.locator('.mobile-poker-table');
    await expect(table).toBeVisible({ timeout: 10000 });

    // Spinner should be visible inside the overlay
    const spinner = page.locator('.reconnecting-spinner');
    await expect(spinner).toBeVisible({ timeout: 5000 });
  });

  test('reconnecting overlay not shown when socket is connected', async ({ page }) => {
    // Set up game page with socket connected
    await setupGamePage(page, true);

    const table = page.locator('.mobile-poker-table');
    await expect(table).toBeVisible({ timeout: 10000 });

    // Give a moment for any overlay to appear
    await page.waitForTimeout(1000);

    // Reconnecting overlay should NOT be visible
    const overlay = page.locator('.mobile-reconnecting-overlay');
    await expect(overlay).not.toBeVisible();
  });

});
