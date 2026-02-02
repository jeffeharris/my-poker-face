import { test, expect } from '@playwright/test';
import { mockGamePageRoutes, buildGameState, setAuthLocalStorage } from '../helpers';

test.describe('PW-16: Reconnecting overlay appears when socket drops', () => {

  test('reconnecting overlay visible when socket is disconnected during game', async ({ page }) => {
    await mockGamePageRoutes(page, { gameState: buildGameState(), socketConnected: false });

    // Navigate directly (not via navigateToGamePage since we need specific navigation)
    await page.goto('/game/test-game-123', { waitUntil: 'commit' });
    await setAuthLocalStorage(page);
    await page.goto('/game/test-game-123');

    const table = page.locator('.mobile-poker-table');
    await expect(table).toBeVisible({ timeout: 10000 });

    const overlay = page.locator('.mobile-reconnecting-overlay');
    await expect(overlay).toBeVisible({ timeout: 5000 });
    await expect(overlay).toContainText('Reconnecting');
  });

  test('reconnecting overlay has spinner element', async ({ page }) => {
    await mockGamePageRoutes(page, { gameState: buildGameState(), socketConnected: false });

    await page.goto('/game/test-game-123', { waitUntil: 'commit' });
    await setAuthLocalStorage(page);
    await page.goto('/game/test-game-123');

    const table = page.locator('.mobile-poker-table');
    await expect(table).toBeVisible({ timeout: 10000 });

    const spinner = page.locator('.reconnecting-spinner');
    await expect(spinner).toBeVisible({ timeout: 5000 });
  });

  test('reconnecting overlay not shown when socket is connected', async ({ page }) => {
    await mockGamePageRoutes(page, { gameState: buildGameState(), socketConnected: true });

    await page.goto('/game/test-game-123', { waitUntil: 'commit' });
    await setAuthLocalStorage(page);
    await page.goto('/game/test-game-123');

    const table = page.locator('.mobile-poker-table');
    await expect(table).toBeVisible({ timeout: 10000 });

    await page.waitForTimeout(1000);

    const overlay = page.locator('.mobile-reconnecting-overlay');
    await expect(overlay).not.toBeVisible();
  });

});
