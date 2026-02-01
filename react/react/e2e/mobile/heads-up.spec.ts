import { test, expect } from '@playwright/test';
import { readFileSync } from 'fs';
import { dirname, join } from 'path';
import { fileURLToPath } from 'url';
import { mockGamePageRoutes, navigateToMenuPage } from '../helpers';

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

const gameStateFixture = JSON.parse(
  readFileSync(join(__dirname, '../fixtures/game-state.json'), 'utf-8')
);

// 2-player heads-up game state: human + 1 AI opponent
const headsUpGameState = {
  ...gameStateFixture,
  players: [
    gameStateFixture.players[0], // TestPlayer (human)
    {
      ...gameStateFixture.players[1], // Batman (AI)
      is_dealer: true,
    },
  ],
  dealer_idx: 1,
  player_options: gameStateFixture.players[0].player_options,
  highest_bet: gameStateFixture.betting_context.highest_bet,
  min_raise: gameStateFixture.betting_context.min_raise_to,
};

test.describe('PW-05: Quick Play 1v1 creates heads-up game with opponent panel', () => {

  test.beforeEach(async ({ page }) => {
    await mockGamePageRoutes(page, { gameState: headsUpGameState });

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

    await navigateToMenuPage(page);

    // Click 1v1 to create heads-up game
    const oneVone = page.locator('.quick-play-btn--1v1');
    await expect(oneVone).toBeVisible();
    await oneVone.click();
    await page.waitForURL('**/game/test-game-123', { timeout: 10000 });
  });

  test('game loads with exactly 1 opponent', async ({ page }) => {
    const table = page.locator('.mobile-poker-table');
    await expect(table).toBeVisible({ timeout: 10000 });

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

    await expect(panel.locator('.panel-header')).toContainText(/reading/i);
    await expect(panel.locator('.panel-header')).toContainText('The Dark Knight');
  });

  test('opponent name and stack visible', async ({ page }) => {
    const table = page.locator('.mobile-poker-table');
    await expect(table).toBeVisible({ timeout: 10000 });

    const opponentName = page.locator('.opponent-name');
    await expect(opponentName).toBeVisible();
    await expect(opponentName).toContainText('The Dark Knight');

    const opponentStack = page.locator('.mobile-opponent.heads-up-avatar .opponent-stack');
    await expect(opponentStack).toBeVisible();
  });
});
