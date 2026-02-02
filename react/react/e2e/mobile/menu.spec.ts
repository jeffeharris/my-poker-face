import { test, expect } from '@playwright/test';
import { mockMenuPageRoutes, navigateToMenuPage } from '../helpers';

test.describe('PW-03: Game menu renders on mobile with quick play options and guest locks', () => {

  test.describe('Guest user', () => {
    test.beforeEach(async ({ page }) => {
      await mockMenuPageRoutes(page, { isGuest: true });
      await navigateToMenuPage(page);
    });

    test('menu page loads with three quick play options', async ({ page }) => {
      await expect(page.locator('.quick-play-section')).toBeVisible();
      await expect(page.locator('.quick-play-section__title')).toContainText('Quick Play');

      const quickPlayButtons = page.locator('.quick-play-btn');
      await expect(quickPlayButtons).toHaveCount(3);

      const lightning = page.locator('.quick-play-btn--lightning');
      await expect(lightning).toBeVisible();
      await expect(lightning.locator('.quick-play-btn__label')).toContainText('Lightning');

      const oneVOne = page.locator('.quick-play-btn--1v1');
      await expect(oneVOne).toBeVisible();
      await expect(oneVOne.locator('.quick-play-btn__label')).toContainText('1v1');

      const classic = page.locator('.quick-play-btn--random');
      await expect(classic).toBeVisible();
      await expect(classic.locator('.quick-play-btn__label')).toContainText('Classic');
    });

    test('quick play buttons are clickable (not disabled)', async ({ page }) => {
      await page.route('**/api/new-game', route =>
        route.fulfill({ json: { game_id: 'test-game-123' } })
      );
      await page.route('**/socket.io/**', route =>
        route.fulfill({ body: '' })
      );

      const lightning = page.locator('.quick-play-btn--lightning');
      await expect(lightning).toBeEnabled();

      const oneVOne = page.locator('.quick-play-btn--1v1');
      await expect(oneVOne).toBeEnabled();

      const classic = page.locator('.quick-play-btn--random');
      await expect(classic).toBeEnabled();
    });

    test('Custom Game and Themed Game show lock for guests', async ({ page }) => {
      const customGame = page.locator('.menu-option.custom-game');
      await expect(customGame).toBeVisible();
      await expect(customGame).toHaveClass(/menu-option--locked/);
      await expect(customGame).toBeDisabled();

      const themedGame = page.locator('.menu-option.themed-game');
      await expect(themedGame).toBeVisible();
      await expect(themedGame).toHaveClass(/menu-option--locked/);
      await expect(themedGame).toBeDisabled();

      await expect(customGame.locator('.pro-badge')).toBeVisible();
      await expect(themedGame.locator('.pro-badge')).toBeVisible();

      await expect(customGame).toContainText('Sign in with Google to unlock');
      await expect(themedGame).toContainText('Sign in with Google to unlock');
    });

    test('Continue Game option is visible', async ({ page }) => {
      const continueGame = page.locator('.menu-option.continue-game');
      await expect(continueGame).toBeVisible();
      await expect(continueGame).toContainText('Continue Game');
      await expect(continueGame).toContainText('No saved games yet');
    });

    test('MenuBar is visible at top with user info', async ({ page }) => {
      const menuBar = page.locator('.menu-bar');
      await expect(menuBar).toBeVisible();
    });
  });

  test.describe('Registered user', () => {
    test.beforeEach(async ({ page }) => {
      await mockMenuPageRoutes(page, { isGuest: false });
      await navigateToMenuPage(page, { isGuest: false });
    });

    test('Custom Game and Themed Game are NOT locked for registered users', async ({ page }) => {
      const customGame = page.locator('.menu-option.custom-game');
      await expect(customGame).toBeVisible();
      await expect(customGame).not.toHaveClass(/menu-option--locked/);
      await expect(customGame).toBeEnabled();
      await expect(customGame).toContainText('Custom Game');

      const themedGame = page.locator('.menu-option.themed-game');
      await expect(themedGame).toBeVisible();
      await expect(themedGame).not.toHaveClass(/menu-option--locked/);
      await expect(themedGame).toBeEnabled();
      await expect(themedGame).toContainText('Themed Game');

      await expect(customGame.locator('.pro-badge')).toHaveCount(0);
      await expect(themedGame.locator('.pro-badge')).toHaveCount(0);
    });
  });
});
