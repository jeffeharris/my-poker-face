import { test, expect } from '@playwright/test';

test.describe('PW-03: Game menu renders on mobile with quick play options and guest locks', () => {

  test.describe('Guest user', () => {
    test.beforeEach(async ({ page }) => {
      // In dev mode, useAuth bypasses guest restrictions unless VITE_FORCE_GUEST=true.
      // Intercept the useAuth module to patch the env check so guest mode works in tests.
      await page.route('**/@fs/**useAuth**', async route => {
        const response = await route.fetch();
        let body = await response.text();
        // Replace the bypass check so guests remain guests in tests
        body = body.replace(
          /import\.meta\.env\.VITE_FORCE_GUEST\s*!==\s*['"]true['"]/,
          'false'
        );
        await route.fulfill({ response, body });
      });
      // Also intercept Vite's module path format
      await page.route('**/src/hooks/useAuth**', async route => {
        const response = await route.fetch();
        let body = await response.text();
        body = body.replace(
          /import\.meta\.env\.VITE_FORCE_GUEST\s*!==\s*['"]true['"]/,
          'false'
        );
        await route.fulfill({ response, body });
      });

      // Mock auth/me to return guest user
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

      // Mock saved games (empty)
      await page.route('**/api/games', route =>
        route.fulfill({ json: { games: [] } })
      );

      // Mock career stats
      await page.route('**/api/career-stats*', route =>
        route.fulfill({ json: { games_played: 5, games_won: 2, win_rate: 0.4, total_knockouts: 3 } })
      );

      // Mock usage stats
      await page.route('**/api/usage-stats*', route =>
        route.fulfill({ json: { hands_played: 3, hands_limit: 20 } })
      );

      // Mock personalities
      await page.route('**/api/personalities', route =>
        route.fulfill({ json: { personalities: [] } })
      );

      // Mock health
      await page.route('**/health', route =>
        route.fulfill({ json: { status: 'ok' } })
      );

      // Set localStorage so ProtectedRoute allows access
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
    });

    test('menu page loads with three quick play options', async ({ page }) => {
      // Quick Play section visible
      await expect(page.locator('.quick-play-section')).toBeVisible();
      await expect(page.locator('.quick-play-section__title')).toContainText('Quick Play');

      // Three quick play buttons visible
      const quickPlayButtons = page.locator('.quick-play-btn');
      await expect(quickPlayButtons).toHaveCount(3);

      // Lightning button
      const lightning = page.locator('.quick-play-btn--lightning');
      await expect(lightning).toBeVisible();
      await expect(lightning.locator('.quick-play-btn__label')).toContainText('Lightning');

      // 1v1 button
      const oneVOne = page.locator('.quick-play-btn--1v1');
      await expect(oneVOne).toBeVisible();
      await expect(oneVOne.locator('.quick-play-btn__label')).toContainText('1v1');

      // Classic button
      const classic = page.locator('.quick-play-btn--random');
      await expect(classic).toBeVisible();
      await expect(classic.locator('.quick-play-btn__label')).toContainText('Classic');
    });

    test('quick play buttons are clickable (not disabled)', async ({ page }) => {
      // Mock new-game endpoint for click handling
      await page.route('**/api/new-game', route =>
        route.fulfill({ json: { game_id: 'test-game-123' } })
      );
      // Mock socket.io to prevent hanging
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
      // Custom Game is locked
      const customGame = page.locator('.menu-option.custom-game');
      await expect(customGame).toBeVisible();
      await expect(customGame).toHaveClass(/menu-option--locked/);
      await expect(customGame).toBeDisabled();

      // Themed Game is locked
      const themedGame = page.locator('.menu-option.themed-game');
      await expect(themedGame).toBeVisible();
      await expect(themedGame).toHaveClass(/menu-option--locked/);
      await expect(themedGame).toBeDisabled();

      // Both should show "Pro" badge
      await expect(customGame.locator('.pro-badge')).toBeVisible();
      await expect(themedGame.locator('.pro-badge')).toBeVisible();

      // Both should show "Sign in with Google to unlock"
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
      // Mock auth/me to return registered user
      await page.route('**/api/auth/me', route =>
        route.fulfill({
          json: {
            user: {
              id: 'user-456',
              name: 'TestPlayer',
              is_guest: false,
              created_at: '2024-01-01',
              permissions: ['play', 'custom_game', 'themed_game']
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

      // Mock usage stats
      await page.route('**/api/usage-stats*', route =>
        route.fulfill({ json: { hands_played: 3, hands_limit: 20 } })
      );

      // Mock personalities
      await page.route('**/api/personalities', route =>
        route.fulfill({ json: { personalities: [] } })
      );

      // Mock health
      await page.route('**/health', route =>
        route.fulfill({ json: { status: 'ok' } })
      );

      // Set localStorage so ProtectedRoute allows access
      await page.goto('/menu', { waitUntil: 'commit' });
      await page.evaluate(() => {
        localStorage.setItem('currentUser', JSON.stringify({
          id: 'user-456',
          name: 'TestPlayer',
          is_guest: false,
          created_at: '2024-01-01',
          permissions: ['play', 'custom_game', 'themed_game']
        }));
      });
      await page.goto('/menu');
    });

    test('Custom Game and Themed Game are NOT locked for registered users', async ({ page }) => {
      // Custom Game is NOT locked
      const customGame = page.locator('.menu-option.custom-game');
      await expect(customGame).toBeVisible();
      await expect(customGame).not.toHaveClass(/menu-option--locked/);
      await expect(customGame).toBeEnabled();
      await expect(customGame).toContainText('Custom Game');

      // Themed Game is NOT locked
      const themedGame = page.locator('.menu-option.themed-game');
      await expect(themedGame).toBeVisible();
      await expect(themedGame).not.toHaveClass(/menu-option--locked/);
      await expect(themedGame).toBeEnabled();
      await expect(themedGame).toContainText('Themed Game');

      // No Pro badges
      await expect(customGame.locator('.pro-badge')).toHaveCount(0);
      await expect(themedGame.locator('.pro-badge')).toHaveCount(0);
    });
  });
});
