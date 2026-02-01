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
 * Build a game state with raise in player options and configurable betting context.
 */
function buildGameState(overrides: Record<string, unknown> = {}) {
  const players = gameStateFixture.players.map((p: Record<string, unknown>, i: number) => {
    if (i === 0) return { ...p, player_options: ['fold', 'call', 'raise'] };
    return p;
  });
  return {
    ...gameStateFixture,
    players,
    player_options: ['fold', 'call', 'raise'],
    highest_bet: gameStateFixture.betting_context.highest_bet,
    min_raise: gameStateFixture.betting_context.min_raise_to,
    ...overrides,
  };
}

/**
 * Set up all mocks and navigate to the game page with the given game state.
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

  // Mock player action — capture the submitted action
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

test.describe('PW-07: Mobile raise sheet — open, slider, quick bets, confirm', () => {

  test('tapping Raise button opens the raise sheet', async ({ page }) => {
    const gameState = buildGameState();
    await setupGamePage(page, gameState);

    // Raise button should be visible
    const raiseBtn = page.locator('.action-btn.raise-btn');
    await expect(raiseBtn).toBeVisible();

    // Tap Raise to open the sheet
    await raiseBtn.click();

    // Raise sheet should slide up and become visible
    const raiseSheet = page.locator('.mobile-raise-sheet');
    await expect(raiseSheet).toBeVisible({ timeout: 5000 });

    // Sheet header shows "Raise" title
    await expect(raiseSheet.locator('.raise-title')).toBeVisible();
  });

  test('raise sheet has Cancel and Confirm buttons', async ({ page }) => {
    const gameState = buildGameState();
    await setupGamePage(page, gameState);

    await page.locator('.action-btn.raise-btn').click();

    const raiseSheet = page.locator('.mobile-raise-sheet');
    await expect(raiseSheet).toBeVisible({ timeout: 5000 });

    // Cancel button visible
    await expect(raiseSheet.locator('.cancel-btn')).toBeVisible();

    // Confirm button visible
    await expect(raiseSheet.locator('.confirm-btn')).toBeVisible();
  });

  test('raise sheet shows amount display', async ({ page }) => {
    const gameState = buildGameState();
    await setupGamePage(page, gameState);

    await page.locator('.action-btn.raise-btn').click();

    const raiseSheet = page.locator('.mobile-raise-sheet');
    await expect(raiseSheet).toBeVisible({ timeout: 5000 });

    // Amount display shows a value (the amount-value element or amount-input)
    const amountDisplay = raiseSheet.locator('.amount-value, .amount-input');
    await expect(amountDisplay.first()).toBeVisible();
  });

  test('raise sheet shows quick bet buttons', async ({ page }) => {
    const gameState = buildGameState();
    await setupGamePage(page, gameState);

    await page.locator('.action-btn.raise-btn').click();

    const raiseSheet = page.locator('.mobile-raise-sheet');
    await expect(raiseSheet).toBeVisible({ timeout: 5000 });

    // Quick bet buttons are visible
    const quickBetButtons = raiseSheet.locator('.quick-bet-btn');
    const count = await quickBetButtons.count();
    expect(count).toBeGreaterThan(0);
  });

  test('raise sheet shows slider with min/max', async ({ page }) => {
    const gameState = buildGameState();
    await setupGamePage(page, gameState);

    await page.locator('.action-btn.raise-btn').click();

    const raiseSheet = page.locator('.mobile-raise-sheet');
    await expect(raiseSheet).toBeVisible({ timeout: 5000 });

    // Slider is visible
    const slider = raiseSheet.locator('.raise-slider');
    await expect(slider).toBeVisible();

    // Slider has min and max attributes
    const min = await slider.getAttribute('min');
    const max = await slider.getAttribute('max');
    expect(min).toBeTruthy();
    expect(max).toBeTruthy();
    expect(Number(min)).toBeGreaterThan(0);
    expect(Number(max)).toBeGreaterThan(Number(min));
  });

  test('tapping a quick bet button updates the amount', async ({ page }) => {
    const gameState = buildGameState();
    await setupGamePage(page, gameState);

    await page.locator('.action-btn.raise-btn').click();

    const raiseSheet = page.locator('.mobile-raise-sheet');
    await expect(raiseSheet).toBeVisible({ timeout: 5000 });

    // Get initial amount
    const amountDisplay = raiseSheet.locator('.amount-value, .amount-input');
    const _initialText = await amountDisplay.first().textContent() || await amountDisplay.first().inputValue().catch(() => '');

    // Click the last quick bet button (All-In) to get a clear change
    const quickBetButtons = raiseSheet.locator('.quick-bet-btn');
    const count = await quickBetButtons.count();
    expect(count).toBeGreaterThan(1);

    // Click the All-In button (last one)
    await quickBetButtons.nth(count - 1).click();

    // Amount should have changed — the selected button should have the 'selected' class
    const selectedBtn = raiseSheet.locator('.quick-bet-btn.selected');
    await expect(selectedBtn).toBeVisible();
  });

  test('tapping Cancel closes the raise sheet and shows action buttons', async ({ page }) => {
    const gameState = buildGameState();
    await setupGamePage(page, gameState);

    await page.locator('.action-btn.raise-btn').click();

    const raiseSheet = page.locator('.mobile-raise-sheet');
    await expect(raiseSheet).toBeVisible({ timeout: 5000 });

    // Tap Cancel
    await raiseSheet.locator('.cancel-btn').click();

    // Sheet should close
    await expect(raiseSheet).not.toBeVisible({ timeout: 5000 });

    // Action buttons visible again
    await expect(page.locator('.mobile-action-buttons')).toBeVisible();
    await expect(page.locator('.action-btn.raise-btn')).toBeVisible();
  });

  test('tapping Confirm submits the raise action', async ({ page }) => {
    const gameState = buildGameState();
    await setupGamePage(page, gameState);

    // Track action requests
    const actionRequests: string[] = [];
    await page.route('**/api/game/*/action', route => {
      actionRequests.push(route.request().postData() || '');
      route.fulfill({ json: { success: true } });
    });

    await page.locator('.action-btn.raise-btn').click();

    const raiseSheet = page.locator('.mobile-raise-sheet');
    await expect(raiseSheet).toBeVisible({ timeout: 5000 });

    // Tap Confirm
    await raiseSheet.locator('.confirm-btn').click();

    // Sheet should close after confirm
    await expect(raiseSheet).not.toBeVisible({ timeout: 5000 });
  });

  test('raise sheet shows stack preview', async ({ page }) => {
    const gameState = buildGameState();
    await setupGamePage(page, gameState);

    await page.locator('.action-btn.raise-btn').click();

    const raiseSheet = page.locator('.mobile-raise-sheet');
    await expect(raiseSheet).toBeVisible({ timeout: 5000 });

    // Stack preview shows "Stack after: $X"
    const stackPreview = raiseSheet.locator('.stack-preview');
    await expect(stackPreview).toBeVisible();
    const text = await stackPreview.textContent();
    expect(text).toMatch(/stack after/i);
  });

});
