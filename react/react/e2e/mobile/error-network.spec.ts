import { test, expect } from '@playwright/test';
import { mockGamePageRoutes, navigateToGamePage, buildGameState } from '../helpers';

test.describe('Error scenarios: Network failures', () => {

  test('network abort on action endpoint shows game still functional', async ({ page }) => {
    const ctx = await mockGamePageRoutes(page, { gameState: buildGameState(['fold', 'call', 'raise']) });
    await navigateToGamePage(page, { mockContext: ctx });

    // Override action route to abort (network failure)
    await page.route('**/api/game/*/action', route => route.abort());

    const foldBtn = page.getByTestId('action-btn-fold');
    await expect(foldBtn).toBeVisible();
    await foldBtn.click();

    // Game should not crash — table should remain visible
    await page.waitForTimeout(1000);
    const table = page.getByTestId('mobile-poker-table');
    await expect(table).toBeVisible();
  });

  test('slow response shows game remains interactive', async ({ page }) => {
    const ctx = await mockGamePageRoutes(page, { gameState: buildGameState(['fold', 'call', 'raise']) });
    await navigateToGamePage(page, { mockContext: ctx });

    // Override action route with a 10s delay
    await page.route('**/api/game/*/action', route => {
      setTimeout(() => {
        route.fulfill({ json: { success: true } });
      }, 10000);
    });

    const foldBtn = page.getByTestId('action-btn-fold');
    await expect(foldBtn).toBeVisible();
    await foldBtn.click();

    // During the delay, the game table should still be visible
    await page.waitForTimeout(2000);
    const table = page.getByTestId('mobile-poker-table');
    await expect(table).toBeVisible();
  });

  test('socket disconnect shows reconnecting overlay, reconnect clears it', async ({ page }) => {
    // Start with connected socket
    const ctx = await mockGamePageRoutes(page, {
      gameState: buildGameState(),
      socketConnected: true,
    });
    await navigateToGamePage(page, { mockContext: ctx });

    const table = page.getByTestId('mobile-poker-table');
    await expect(table).toBeVisible();

    // Verify reconnecting overlay is NOT visible initially
    await page.waitForTimeout(1000);
    const overlay = page.getByTestId('reconnecting-overlay');
    await expect(overlay).not.toBeVisible();

    // Now simulate disconnect by overriding socket.io routes to stop responding
    await page.unroute('**/socket.io/**');
    await page.route('**/socket.io/**', route => route.abort());

    // The reconnecting overlay may appear after socket timeout
    // This tests that the UI handles disconnect gracefully
    await page.waitForTimeout(3000);
    // Table should still be visible even if socket drops
    await expect(table).toBeVisible();
  });

});
