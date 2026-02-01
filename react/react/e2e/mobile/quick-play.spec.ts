import { test, expect } from '@playwright/test';
import { readFileSync } from 'fs';
import { dirname, join } from 'path';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

const gameStateFixture = JSON.parse(
  readFileSync(join(__dirname, '../fixtures/game-state.json'), 'utf-8')
);

test.describe('PW-04: Quick Play Lightning creates game and mobile table loads', () => {

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

    // Mock auth/me to return guest user
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

    // Mock game state endpoint (usePokerGame fetches /api/game-state/:id)
    // Add top-level player_options and other fields the frontend GameState type expects
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
    let socketConnectSent = false;
    await page.route('**/socket.io/**', route => {
      const url = route.request().url();
      if (url.includes('transport=polling') && route.request().method() === 'GET') {
        if (!url.includes('sid=')) {
          // Engine.IO handshake
          route.fulfill({
            contentType: 'text/plain',
            body: '0{"sid":"fake-sid","upgrades":[],"pingInterval":25000,"pingTimeout":20000}'
          });
        } else if (!socketConnectSent) {
          // First poll after handshake: send Socket.IO CONNECT packet
          // Engine.IO message type 4 + Socket.IO CONNECT type 0 = "40"
          socketConnectSent = true;
          route.fulfill({
            contentType: 'text/plain',
            body: '40{"sid":"fake-socket-sid"}'
          });
        } else {
          // Subsequent polls - noop (keep connection alive)
          route.fulfill({
            contentType: 'text/plain',
            body: '6'
          });
        }
      } else if (route.request().method() === 'POST') {
        // Client sending data via polling
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

    // Click Lightning to create game and navigate to game page
    const lightning = page.locator('.quick-play-btn--lightning');
    await expect(lightning).toBeVisible();
    await lightning.click();
    await page.waitForURL('**/game/test-game-123', { timeout: 10000 });
  });

  test('game creation triggers navigation to game page', async ({ page }) => {
    await expect(page).toHaveURL(/\/game\/test-game-123/);
  });

  test('mobile poker table renders with opponents', async ({ page }) => {
    // Mobile poker table should render
    const table = page.locator('.mobile-poker-table');
    await expect(table).toBeVisible({ timeout: 10000 });

    // Opponents strip visible
    const opponents = page.locator('.mobile-opponents');
    await expect(opponents).toBeVisible();

    // Should see opponent names (Batman and Gandalf)
    await expect(page.locator('.opponent-name').first()).toBeVisible();
  });

  test('community cards area visible', async ({ page }) => {
    const table = page.locator('.mobile-poker-table');
    await expect(table).toBeVisible({ timeout: 10000 });

    // Community cards section visible (empty for pre-flop but area exists)
    const community = page.locator('.mobile-community');
    await expect(community).toBeVisible();
  });

  test('hero section with player cards visible', async ({ page }) => {
    const table = page.locator('.mobile-poker-table');
    await expect(table).toBeVisible({ timeout: 10000 });

    // Hero section visible
    const hero = page.locator('.mobile-hero');
    await expect(hero).toBeVisible();

    // Hero cards visible (player has A♠ K♥)
    const heroCards = page.locator('.hero-cards');
    await expect(heroCards).toBeVisible();
  });

  test('action buttons visible (fold, call, raise)', async ({ page }) => {
    const table = page.locator('.mobile-poker-table');
    await expect(table).toBeVisible({ timeout: 10000 });

    // Action buttons container visible
    const actionButtons = page.locator('.mobile-action-buttons');
    await expect(actionButtons).toBeVisible();

    // Fold button
    const foldBtn = page.locator('.action-btn.fold-btn');
    await expect(foldBtn).toBeVisible();

    // Call button
    const callBtn = page.locator('.action-btn.call-btn');
    await expect(callBtn).toBeVisible();

    // Raise button
    const raiseBtn = page.locator('.action-btn.raise-btn');
    await expect(raiseBtn).toBeVisible();
  });

  test('pot display shows correct amount', async ({ page }) => {
    const table = page.locator('.mobile-poker-table');
    await expect(table).toBeVisible({ timeout: 10000 });

    // Pot display visible with amount
    const pot = page.locator('.mobile-pot');
    await expect(pot).toBeVisible();
    // The pot total is 150 in the fixture
    await expect(pot).toContainText('150');
  });
});
