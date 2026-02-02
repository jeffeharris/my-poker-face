import { test, expect } from '@playwright/test';
import { mockGamePageRoutes, buildGameState, setAuthLocalStorage } from '../helpers';

test.describe('PW-17: Mobile navigation — back button returns to menu', () => {

  test('from game page, back button in MenuBar navigates to /menu', async ({ page }) => {
    await mockGamePageRoutes(page, { isGuest: false, gameState: buildGameState() });

    await page.goto('/game/test-game-123', { waitUntil: 'commit' });
    await setAuthLocalStorage(page, { isGuest: false });
    await page.goto('/game/test-game-123');

    const table = page.getByTestId('mobile-poker-table');
    await expect(table).toBeVisible({ timeout: 10000 });

    const backButton = page.locator('.menu-bar__back');
    await expect(backButton).toBeVisible({ timeout: 5000 });
    await backButton.click();

    await page.waitForURL('**/menu', { timeout: 5000 });
    await expect(page).toHaveURL(/\/menu/);
  });

  test('from career stats, back button navigates to /menu', async ({ page }) => {
    await mockGamePageRoutes(page, { isGuest: false, gameState: buildGameState() });

    await page.goto('/stats', { waitUntil: 'commit' });
    await setAuthLocalStorage(page, { isGuest: false });
    await page.goto('/stats');

    const menuBar = page.locator('.menu-bar');
    await expect(menuBar).toBeVisible({ timeout: 10000 });

    const backButton = page.locator('.menu-bar__back');
    await expect(backButton).toBeVisible({ timeout: 5000 });
    await backButton.click();

    await page.waitForURL('**/menu', { timeout: 5000 });
    await expect(page).toHaveURL(/\/menu/);
  });

  test('from custom game config, back button navigates to /menu', async ({ page }) => {
    await mockGamePageRoutes(page, { isGuest: false, gameState: buildGameState() });

    await page.goto('/game/new/custom', { waitUntil: 'commit' });
    await setAuthLocalStorage(page, { isGuest: false });
    await page.goto('/game/new/custom');

    const menuBar = page.locator('.menu-bar');
    await expect(menuBar).toBeVisible({ timeout: 10000 });

    const backButton = page.locator('.menu-bar__back');
    await expect(backButton).toBeVisible({ timeout: 5000 });
    await backButton.click();

    await page.waitForURL('**/menu', { timeout: 5000 });
    await expect(page).toHaveURL(/\/menu/);
  });

  test('browser back button works from stats to menu', async ({ page }) => {
    await mockGamePageRoutes(page, { isGuest: false, gameState: buildGameState() });

    await page.goto('/menu', { waitUntil: 'commit' });
    await setAuthLocalStorage(page, { isGuest: false });
    await page.goto('/menu');

    await expect(page.locator('.quick-play-section')).toBeVisible({ timeout: 10000 });

    await page.goto('/stats');

    const menuBar = page.locator('.menu-bar');
    await expect(menuBar).toBeVisible({ timeout: 10000 });

    await page.goBack();

    await page.waitForURL('**/menu', { timeout: 5000 });
    await expect(page).toHaveURL(/\/menu/);
  });

});
