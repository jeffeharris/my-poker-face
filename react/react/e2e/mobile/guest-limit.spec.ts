import { test, expect } from '@playwright/test';

/**
 * Set up all mocks and navigate to the menu page.
 * The guest limit modal is triggered at the App level when
 * usage-stats returns hands_limit_reached: true.
 */
async function setupPage(
  page: import('@playwright/test').Page,
  opts: {
    handsPlayed?: number;
    handsLimit?: number;
    handsLimitReached?: boolean;
  } = {}
) {
  const handsPlayed = opts.handsPlayed ?? 20;
  const handsLimit = opts.handsLimit ?? 20;
  const handsLimitReached = opts.handsLimitReached ?? true;

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

  // Mock usage stats — hands_limit_reached triggers the modal
  await page.route('**/api/usage-stats*', route =>
    route.fulfill({
      json: {
        hands_played: handsPlayed,
        hands_limit: handsLimit,
        hands_limit_reached: handsLimitReached,
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

  // Mock socket.io (no game events needed for menu)
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
}

test.describe('PW-14: Guest limit modal appears and offers upgrade', () => {

  test('guest limit modal appears when hand limit is reached', async ({ page }) => {
    await setupPage(page);

    const overlay = page.locator('.guest-limit-modal__overlay');
    await expect(overlay).toBeVisible({ timeout: 10000 });

    const modal = page.locator('.guest-limit-modal');
    await expect(modal).toBeVisible();
  });

  test('modal shows shield icon and correct title with hand count', async ({ page }) => {
    await setupPage(page, { handsPlayed: 20, handsLimit: 20 });

    const overlay = page.locator('.guest-limit-modal__overlay');
    await expect(overlay).toBeVisible({ timeout: 10000 });

    // Title shows hand count
    const title = page.locator('.guest-limit-modal__title');
    await expect(title).toBeVisible();
    await expect(title).toContainText("You've played 20 hands!");

    // Shield icon is present (rendered as svg inside the icon container)
    const icon = page.locator('.guest-limit-modal__icon');
    await expect(icon).toBeVisible();
  });

  test('modal shows 4 benefits', async ({ page }) => {
    await setupPage(page);

    const overlay = page.locator('.guest-limit-modal__overlay');
    await expect(overlay).toBeVisible({ timeout: 10000 });

    const benefits = page.locator('.guest-limit-modal__benefit');
    await expect(benefits).toHaveCount(4);

    // Verify benefit text content
    await expect(benefits.nth(0)).toContainText('Unlimited hands');
    await expect(benefits.nth(1)).toContainText('Up to 9 AI opponents');
    await expect(benefits.nth(2)).toContainText('Custom game wizard');
    await expect(benefits.nth(3)).toContainText('Themed game experiences');
  });

  test('Sign in with Google CTA button is visible', async ({ page }) => {
    await setupPage(page);

    const overlay = page.locator('.guest-limit-modal__overlay');
    await expect(overlay).toBeVisible({ timeout: 10000 });

    const cta = page.locator('.guest-limit-modal__cta');
    await expect(cta).toBeVisible();
    await expect(cta).toContainText('Sign in with Google');
  });

  test('Return to Main Menu button is visible and navigates to menu', async ({ page }) => {
    await setupPage(page);

    const overlay = page.locator('.guest-limit-modal__overlay');
    await expect(overlay).toBeVisible({ timeout: 10000 });

    const secondary = page.locator('.guest-limit-modal__secondary');
    await expect(secondary).toBeVisible();
    await expect(secondary).toContainText('Return to Main Menu');

    // Click the return to menu button
    await secondary.click();

    // Modal should dismiss after clicking return to menu
    await expect(overlay).not.toBeVisible({ timeout: 5000 });
  });

  test('modal does NOT appear when hand limit is not reached', async ({ page }) => {
    await setupPage(page, { handsPlayed: 3, handsLimit: 20, handsLimitReached: false });

    // Wait for the page to load fully
    await page.waitForTimeout(2000);

    // Modal should not be visible
    const overlay = page.locator('.guest-limit-modal__overlay');
    await expect(overlay).not.toBeVisible();
  });

});
