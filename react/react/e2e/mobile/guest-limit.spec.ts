import { test, expect } from '@playwright/test';
import { mockMenuPageRoutes, navigateToMenuPage } from '../helpers';

test.describe('PW-14: Guest limit modal appears and offers upgrade', () => {

  test('guest limit modal appears when hand limit is reached', async ({ page }) => {
    await mockMenuPageRoutes(page, { isGuest: true, handsPlayed: 20, handsLimit: 20, handsLimitReached: true });
    await navigateToMenuPage(page);

    const overlay = page.locator('.guest-limit-modal__overlay');
    await expect(overlay).toBeVisible({ timeout: 10000 });

    const modal = page.locator('.guest-limit-modal');
    await expect(modal).toBeVisible();
  });

  test('modal shows shield icon and correct title with hand count', async ({ page }) => {
    await mockMenuPageRoutes(page, { isGuest: true, handsPlayed: 20, handsLimit: 20, handsLimitReached: true });
    await navigateToMenuPage(page);

    const overlay = page.locator('.guest-limit-modal__overlay');
    await expect(overlay).toBeVisible({ timeout: 10000 });

    const title = page.locator('.guest-limit-modal__title');
    await expect(title).toBeVisible();
    await expect(title).toContainText("You've played 20 hands!");

    const icon = page.locator('.guest-limit-modal__icon');
    await expect(icon).toBeVisible();
  });

  test('modal shows 4 benefits', async ({ page }) => {
    await mockMenuPageRoutes(page, { isGuest: true, handsPlayed: 20, handsLimit: 20, handsLimitReached: true });
    await navigateToMenuPage(page);

    const overlay = page.locator('.guest-limit-modal__overlay');
    await expect(overlay).toBeVisible({ timeout: 10000 });

    const benefits = page.locator('.guest-limit-modal__benefit');
    await expect(benefits).toHaveCount(4);

    await expect(benefits.nth(0)).toContainText('Unlimited hands');
    await expect(benefits.nth(1)).toContainText('Up to 9 AI opponents');
    await expect(benefits.nth(2)).toContainText('Custom game wizard');
    await expect(benefits.nth(3)).toContainText('Themed game experiences');
  });

  test('Sign in with Google CTA button is visible', async ({ page }) => {
    await mockMenuPageRoutes(page, { isGuest: true, handsPlayed: 20, handsLimit: 20, handsLimitReached: true });
    await navigateToMenuPage(page);

    const overlay = page.locator('.guest-limit-modal__overlay');
    await expect(overlay).toBeVisible({ timeout: 10000 });

    const cta = page.locator('.guest-limit-modal__cta');
    await expect(cta).toBeVisible();
    await expect(cta).toContainText('Sign in with Google');
  });

  test('Return to Main Menu button is visible and navigates to menu', async ({ page }) => {
    await mockMenuPageRoutes(page, { isGuest: true, handsPlayed: 20, handsLimit: 20, handsLimitReached: true });
    await navigateToMenuPage(page);

    const overlay = page.locator('.guest-limit-modal__overlay');
    await expect(overlay).toBeVisible({ timeout: 10000 });

    const secondary = page.locator('.guest-limit-modal__secondary');
    await expect(secondary).toBeVisible();
    await expect(secondary).toContainText('Return to Main Menu');

    await secondary.click();

    await expect(overlay).not.toBeVisible({ timeout: 5000 });
  });

  test('modal does NOT appear when hand limit is not reached', async ({ page }) => {
    await mockMenuPageRoutes(page, { isGuest: true, handsPlayed: 3, handsLimit: 20, handsLimitReached: false });
    await navigateToMenuPage(page);

    await page.waitForTimeout(2000);

    const overlay = page.locator('.guest-limit-modal__overlay');
    await expect(overlay).not.toBeVisible();
  });

});
