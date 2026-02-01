import { test, expect } from '@playwright/test';
import { readFileSync } from 'fs';
import { dirname, join } from 'path';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

const baseGameState = JSON.parse(
  readFileSync(join(__dirname, '../fixtures/game-state.json'), 'utf-8')
);

// 2-player heads-up game state: human + 1 AI opponent
const headsUpGameState = {
  ...baseGameState,
  players: [
    baseGameState.players[0], // TestPlayer (human)
    {
      ...baseGameState.players[1], // Batman (AI)
      is_dealer: true,
    },
  ],
  dealer_idx: 1,
  player_options: baseGameState.players[0].player_options,
  highest_bet: baseGameState.betting_context.highest_bet,
  min_raise: baseGameState.betting_context.min_raise_to,
};

test.describe('PW-05: Quick Play 1v1 creates heads-up game with opponent panel', () => {

  test.beforeEach(async ({ page }) => {
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

    // Mock new-game endpoint
    await page.route('**/api/new-game', route =>
      route.fulfill({ json: { game_id: 'test-game-123' } })
    );

    // Mock game state endpoint with heads-up (2-player) state
    await page.route('**/api/game-state/test-game-123', route =>
      route.fulfill({ json: headsUpGameState })
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

    // Mock pressure-stats endpoint for HeadsUpOpponentPanel
    await page.route('**/api/game/*/pressure-stats', route =>
      route.fulfill({
        json: {
          player_summaries: {
            Batman: {
              tilt_level: 0.1,
              tilt_category: 'none',
              emotional_state: 'calm',
              narrative: 'Playing a steady game.',
              inner_voice: 'I need to stay focused.'
            }
          }
        }
      })
    );

    // Mock memory-debug endpoint for HeadsUpOpponentPanel
    await page.route('**/api/game/*/memory-debug', route =>
      route.fulfill({
        json: {
          opponent_models: {
            Batman: {
              TestPlayer: {
                vpip: 0.65,
                aggression_factor: 2.1,
                hands_observed: 8,
                play_style: 'Loose-Aggressive'
              }
            }
          }
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

    // Set localStorage for guest user and navigate to menu
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
    await page.goto('/menu');

    // Click 1v1 to create heads-up game
    const oneVone = page.locator('.quick-play-btn--1v1');
    await expect(oneVone).toBeVisible();
    await oneVone.click();
    await page.waitForURL('**/game/test-game-123', { timeout: 10000 });
  });

  test('game loads with exactly 1 opponent', async ({ page }) => {
    const table = page.locator('.mobile-poker-table');
    await expect(table).toBeVisible({ timeout: 10000 });

    // Should have exactly 1 opponent in the opponents strip
    const opponents = page.locator('.mobile-opponent');
    await expect(opponents).toHaveCount(1);
  });

  test('opponent has heads-up-avatar class', async ({ page }) => {
    const table = page.locator('.mobile-poker-table');
    await expect(table).toBeVisible({ timeout: 10000 });

    const headsUpAvatar = page.locator('.mobile-opponent.heads-up-avatar');
    await expect(headsUpAvatar).toBeVisible();
  });

  test('HeadsUpOpponentPanel renders', async ({ page }) => {
    const table = page.locator('.mobile-poker-table');
    await expect(table).toBeVisible({ timeout: 10000 });

    const panel = page.locator('.heads-up-opponent-panel');
    await expect(panel).toBeVisible({ timeout: 10000 });
  });

  test('panel shows "Reading" header with opponent name', async ({ page }) => {
    const table = page.locator('.mobile-poker-table');
    await expect(table).toBeVisible({ timeout: 10000 });

    const panel = page.locator('.heads-up-opponent-panel');
    await expect(panel).toBeVisible({ timeout: 10000 });

    // Panel header should say "Reading [nickname]..." (uses nickname "The Dark Knight")
    await expect(panel.locator('.panel-header')).toContainText(/reading/i);
    await expect(panel.locator('.panel-header')).toContainText('The Dark Knight');
  });

  test('opponent name and stack visible', async ({ page }) => {
    const table = page.locator('.mobile-poker-table');
    await expect(table).toBeVisible({ timeout: 10000 });

    // Opponent name visible
    const opponentName = page.locator('.opponent-name');
    await expect(opponentName).toBeVisible();
    // Displays nickname when available
    await expect(opponentName).toContainText('The Dark Knight');

    // Opponent stack visible
    const opponentStack = page.locator('.mobile-opponent.heads-up-avatar .opponent-stack');
    await expect(opponentStack).toBeVisible();
  });
});
