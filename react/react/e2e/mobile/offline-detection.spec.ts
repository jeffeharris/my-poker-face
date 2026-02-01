import { test, expect } from '@playwright/test';

/**
 * Set up mocks and navigate to the menu page as an authenticated guest.
 */
async function setupPage(page: import('@playwright/test').Page) {
  // Mock auth - guest user
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

  // Mock usage stats — not at limit
  await page.route('**/api/usage-stats*', route =>
    route.fulfill({
      json: {
        hands_played: 3,
        hands_limit: 20,
        hands_limit_reached: false,
        max_opponents: 3,
        max_active_games: 1,
        is_guest: true
      }
    })
  );

  // Mock personalities
  await page.route('**/api/personalities', route =>
    route.fulfill({ json: { personalities: [] } })
  );

  // Mock health
  await page.route('**/health', route =>
    route.fulfill({ json: { status: 'ok' } })
  );

  // Mock socket.io
  await page.route('**/socket.io/**', route => {
    const url = route.request().url();
    if (url.includes('transport=polling') && route.request().method() === 'GET') {
      if (!url.includes('sid=')) {
        route.fulfill({
          contentType: 'text/plain',
          body: '0{"sid":"fake-sid","upgrades":[],"pingInterval":25000,"pingTimeout":20000}'
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

  // Set localStorage for guest user and navigate
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
  // Wait for the page to be fully loaded
  await page.waitForLoadState('networkidle');
}

test.describe('PW-15: Offline detection shows banner on mobile', () => {

  test('going offline shows "Connection lost" toast', async ({ page }) => {
    await setupPage(page);

    // Simulate going offline
    await page.evaluate(() => {
      window.dispatchEvent(new Event('offline'));
    });

    // The useOnlineStatus hook shows a toast.error with this message
    const offlineToast = page.getByText(/connection lost/i);
    await expect(offlineToast).toBeVisible({ timeout: 5000 });
  });

  test('going back online shows "Back online" toast', async ({ page }) => {
    await setupPage(page);

    // Go offline first
    await page.evaluate(() => {
      window.dispatchEvent(new Event('offline'));
    });

    // Verify offline toast appears
    const offlineToast = page.getByText(/connection lost/i);
    await expect(offlineToast).toBeVisible({ timeout: 5000 });

    // Go back online
    await page.evaluate(() => {
      window.dispatchEvent(new Event('online'));
    });

    // "Back online" toast should appear
    const onlineToast = page.getByText(/back online/i);
    await expect(onlineToast).toBeVisible({ timeout: 5000 });
  });

  test('offline toast persists until user comes back online', async ({ page }) => {
    await setupPage(page);

    // Go offline
    await page.evaluate(() => {
      window.dispatchEvent(new Event('offline'));
    });

    const offlineToast = page.getByText(/connection lost/i);
    await expect(offlineToast).toBeVisible({ timeout: 5000 });

    // Wait a bit — the offline toast has duration: Infinity, so it should persist
    await page.waitForTimeout(2000);
    await expect(offlineToast).toBeVisible();

    // Go online — offline toast should be dismissed
    await page.evaluate(() => {
      window.dispatchEvent(new Event('online'));
    });

    // The "Connection lost" toast should disappear
    await expect(offlineToast).not.toBeVisible({ timeout: 5000 });
  });

});
