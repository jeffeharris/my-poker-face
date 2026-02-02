import { test, expect } from '@playwright/test';
import { mockMenuPageRoutes, isRealMode } from '../helpers';

test.describe('PW-02: Guest login flow on mobile', () => {
  test.beforeEach(async ({ page }) => {
    await mockMenuPageRoutes(page, { authenticated: false });
  });

  test('login page renders with banner, name input, and buttons', async ({ page }) => {
    await page.goto('/login');

    await expect(page.locator('.login-form__banner')).toBeVisible();

    const nameInput = page.locator('.login-form__input');
    await expect(nameInput).toBeVisible();
    await expect(nameInput).toHaveAttribute('placeholder', /name/i);

    const guestButton = page.locator('.login-form__button--primary');
    await expect(guestButton).toBeVisible();
    await expect(guestButton).toContainText('Play as Guest');

    const googleButton = page.locator('.login-form__button--google');
    await expect(googleButton).toBeVisible();
    await expect(googleButton).toContainText('Sign in with Google');
  });

  test('name input accepts text with max 20 characters', async ({ page }) => {
    await page.goto('/login');

    const nameInput = page.locator('.login-form__input');
    await nameInput.fill('MobilePlayer');
    await expect(nameInput).toHaveValue('MobilePlayer');

    await nameInput.fill('A'.repeat(25));
    const value = await nameInput.inputValue();
    expect(value.length).toBeLessThanOrEqual(20);
  });

  test('entering name and clicking "Play as Guest" navigates to /menu', async ({ page }) => {
    if (!isRealMode()) {
      // Mock the login endpoint (in real mode, the real backend handles auth)
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

      await page.goto('/login');

      const nameInput = page.locator('.login-form__input');
      await nameInput.fill('MobilePlayer');

      loggedIn = true;

      const guestButton = page.locator('.login-form__button--primary');
      await guestButton.click();
    } else {
      // Real mode: use real backend auth
      await page.goto('/login');

      const nameInput = page.locator('.login-form__input');
      await nameInput.fill('MobilePlayer');

      const guestButton = page.locator('.login-form__button--primary');
      await guestButton.click();
    }

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
