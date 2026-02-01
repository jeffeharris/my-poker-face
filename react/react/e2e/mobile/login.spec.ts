import { test, expect } from '@playwright/test';
import { mockAPIRoutes } from '../helpers';

test.describe('PW-02: Guest login flow on mobile', () => {
  test.beforeEach(async ({ page }) => {
    // Mock auth/me to return 401 initially (unauthenticated)
    await page.route('**/api/auth/me', route =>
      route.fulfill({ status: 401, json: { error: 'Not authenticated' } })
    );

    // Mock health endpoint
    await page.route('**/health', route =>
      route.fulfill({ json: { status: 'ok' } })
    );
  });

  test('login page renders with banner, name input, and buttons', async ({ page }) => {
    await page.goto('/login');

    // Banner image visible
    await expect(page.locator('.login-form__banner')).toBeVisible();

    // Name input visible
    const nameInput = page.locator('.login-form__input');
    await expect(nameInput).toBeVisible();
    await expect(nameInput).toHaveAttribute('placeholder', /name/i);

    // "Play as Guest" button visible
    const guestButton = page.locator('.login-form__button--primary');
    await expect(guestButton).toBeVisible();
    await expect(guestButton).toContainText('Play as Guest');

    // Google sign-in button visible
    const googleButton = page.locator('.login-form__button--google');
    await expect(googleButton).toBeVisible();
    await expect(googleButton).toContainText('Sign in with Google');
  });

  test('name input accepts text with max 20 characters', async ({ page }) => {
    await page.goto('/login');

    const nameInput = page.locator('.login-form__input');
    await nameInput.fill('MobilePlayer');
    await expect(nameInput).toHaveValue('MobilePlayer');

    // Input should enforce max length of 20
    await nameInput.fill('A'.repeat(25));
    const value = await nameInput.inputValue();
    expect(value.length).toBeLessThanOrEqual(20);
  });

  test('entering name and clicking "Play as Guest" navigates to /menu', async ({ page }) => {
    // First, let auth/me return 401 so login page shows
    // (already set in beforeEach)

    // Mock the login endpoint
    await page.route('**/api/auth/login', route =>
      route.fulfill({
        json: {
          success: true,
          user: { id: 'guest-123', name: 'MobilePlayer', is_guest: true, created_at: '2024-01-01', permissions: ['play'] },
          token: 'fake-token-123'
        }
      })
    );

    // After login succeeds, auth/me should return the user for the menu page
    // We need to unroute and re-route auth/me after login
    let loggedIn = false;
    await page.unroute('**/api/auth/me');
    await page.route('**/api/auth/me', route => {
      if (loggedIn) {
        route.fulfill({
          json: { user: { id: 'guest-123', name: 'MobilePlayer', is_guest: true, created_at: '2024-01-01', permissions: ['play'] } }
        });
      } else {
        route.fulfill({ status: 401, json: { error: 'Not authenticated' } });
      }
    });

    // Mock menu page endpoints
    await page.route('**/api/games', route =>
      route.fulfill({ json: { games: [] } })
    );
    await page.route('**/api/usage-stats*', route =>
      route.fulfill({ json: { hands_played: 3, hands_limit: 20 } })
    );
    await page.route('**/api/career-stats*', route =>
      route.fulfill({ json: { games_played: 5, games_won: 2, win_rate: 0.4, total_knockouts: 3 } })
    );
    await page.route('**/api/personalities', route =>
      route.fulfill({ json: { personalities: [] } })
    );

    await page.goto('/login');

    const nameInput = page.locator('.login-form__input');
    await nameInput.fill('MobilePlayer');

    // Mark as logged in before clicking so auth/me returns user on navigation
    loggedIn = true;

    const guestButton = page.locator('.login-form__button--primary');
    await guestButton.click();

    // Should navigate to /menu
    await page.waitForURL('**/menu', { timeout: 10000 });
    expect(page.url()).toContain('/menu');
  });

  test('footer links for Privacy Policy and Terms of Service', async ({ page }) => {
    await page.goto('/login');

    const footer = page.locator('.login-form__footer');
    await expect(footer.getByText('Privacy Policy')).toBeVisible();
    await expect(footer.getByText('Terms of Service')).toBeVisible();
  });
});
