import { test, expect } from '@playwright/test';

test.describe('PW-01: Landing page renders correctly on mobile', () => {
  test.beforeEach(async ({ page }) => {
    // Mock auth/me to return 401 (unauthenticated) so landing page shows
    await page.route('**/api/auth/me', route =>
      route.fulfill({ status: 401, json: { error: 'Not authenticated' } })
    );

    // Mock health endpoint
    await page.route('**/health', route =>
      route.fulfill({ json: { status: 'ok' } })
    );

    await page.goto('/');
  });

  test('page loads at / without errors', async ({ page }) => {
    await expect(page.locator('.landing')).toBeVisible();
  });

  test('banner image is visible', async ({ page }) => {
    await expect(page.locator('.landing__banner img')).toBeVisible();
  });

  test('hero tagline text is visible', async ({ page }) => {
    await expect(page.locator('.landing__hero')).toBeVisible();
    await expect(page.locator('.landing__tagline')).toContainText(
      'Poker against AI that feels human'
    );
  });

  test('all 4 feature blocks are visible with correct labels', async ({ page }) => {
    const features = page.locator('.landing__feature');
    await expect(features).toHaveCount(4);

    await expect(page.getByText('Real Personalities')).toBeVisible();
    await expect(page.getByText('Emotions Matter')).toBeVisible();
    await expect(page.getByText('Table Talk')).toBeVisible();
    await expect(page.getByText('Play Anywhere')).toBeVisible();
  });

  test('"Play Now" CTA button is visible and clickable', async ({ page }) => {
    const playNowButton = page.locator('.landing__button--primary', { hasText: 'Play Now' });
    await expect(playNowButton).toBeVisible();
    await expect(playNowButton).toBeEnabled();
  });

  test('footer links are visible', async ({ page }) => {
    await expect(page.locator('.landing__footer').getByText('Privacy Policy')).toBeVisible();
    await expect(page.locator('.landing__footer').getByText('Terms of Service')).toBeVisible();
  });

  test('clicking "Play Now" navigates to /login', async ({ page }) => {
    const playNowButton = page.locator('.landing__button--primary', { hasText: 'Play Now' });
    await playNowButton.click();
    await page.waitForURL('**/login');
    expect(page.url()).toContain('/login');
  });
});
