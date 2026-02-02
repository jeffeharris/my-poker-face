import { test, expect } from '@playwright/test';
import { mockMenuPageRoutes, navigateToMenuPage } from '../helpers';

test.describe('PW-15: Offline detection shows banner on mobile', () => {

  test('going offline shows "Connection lost" toast', async ({ page }) => {
    await mockMenuPageRoutes(page, { isGuest: true });
    await navigateToMenuPage(page);
    await page.waitForLoadState('networkidle');

    await page.evaluate(() => {
      window.dispatchEvent(new Event('offline'));
    });

    const offlineToast = page.getByText(/connection lost/i);
    await expect(offlineToast).toBeVisible({ timeout: 5000 });
  });

  test('going back online shows "Back online" toast', async ({ page }) => {
    await mockMenuPageRoutes(page, { isGuest: true });
    await navigateToMenuPage(page);
    await page.waitForLoadState('networkidle');

    await page.evaluate(() => {
      window.dispatchEvent(new Event('offline'));
    });

    const offlineToast = page.getByText(/connection lost/i);
    await expect(offlineToast).toBeVisible({ timeout: 5000 });

    await page.evaluate(() => {
      window.dispatchEvent(new Event('online'));
    });

    const onlineToast = page.getByText(/back online/i);
    await expect(onlineToast).toBeVisible({ timeout: 5000 });
  });

  test('offline toast persists until user comes back online', async ({ page }) => {
    await mockMenuPageRoutes(page, { isGuest: true });
    await navigateToMenuPage(page);
    await page.waitForLoadState('networkidle');

    await page.evaluate(() => {
      window.dispatchEvent(new Event('offline'));
    });

    const offlineToast = page.getByText(/connection lost/i);
    await expect(offlineToast).toBeVisible({ timeout: 5000 });

    await page.waitForTimeout(2000);
    await expect(offlineToast).toBeVisible();

    await page.evaluate(() => {
      window.dispatchEvent(new Event('online'));
    });

    await expect(offlineToast).not.toBeVisible({ timeout: 5000 });
  });

});
