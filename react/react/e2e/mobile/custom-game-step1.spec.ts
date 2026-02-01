import { test, expect } from '@playwright/test';

const personalitiesResponse = {
  success: true,
  personalities: {
    Batman: {
      name: 'Batman',
      play_style: 'Tight-Aggressive',
      personality_traits: { bluff_tendency: 0.3, aggression: 0.8, chattiness: 0.2, emoji_usage: 0.1 },
    },
    Gandalf: {
      name: 'Gandalf',
      play_style: 'Calculated',
      personality_traits: { bluff_tendency: 0.2, aggression: 0.4, chattiness: 0.9, emoji_usage: 0.3 },
    },
    'Gordon Ramsay': {
      name: 'Gordon Ramsay',
      play_style: 'Aggressive',
      personality_traits: { bluff_tendency: 0.6, aggression: 0.9, chattiness: 0.8, emoji_usage: 0.5 },
    },
    Cleopatra: {
      name: 'Cleopatra',
      play_style: 'Loose-Aggressive',
      personality_traits: { bluff_tendency: 0.7, aggression: 0.6, chattiness: 0.7, emoji_usage: 0.4 },
    },
    Einstein: {
      name: 'Einstein',
      play_style: 'Tight-Passive',
      personality_traits: { bluff_tendency: 0.1, aggression: 0.2, chattiness: 0.5, emoji_usage: 0.2 },
    },
  },
};

/**
 * Navigate through step 0 and advance to step 1 (Settings).
 * Fills opponents randomly then clicks Next.
 */
async function navigateToStep1(page: import('@playwright/test').Page) {
  // Fill opponents randomly
  await page.locator('.fill-btn--random').click();
  await expect(page.locator('.player-card')).toHaveCount(3);

  // Click Next to advance to step 1
  await page.locator('.wizard-nav__btn--next').click();

  // Wait for Settings step to be active
  await expect(page.locator('.wizard-step').nth(1)).toHaveClass(/wizard-step--active/);
}

test.describe('PW-19: Custom game wizard step 1 — game settings on mobile', () => {
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
            permissions: ['play', 'custom_game', 'themed_game'],
          },
        },
      })
    );

    // Mock personalities endpoint
    await page.route('**/api/personalities', route =>
      route.fulfill({ json: personalitiesResponse })
    );

    // Mock user-models endpoint
    await page.route('**/api/user-models', route =>
      route.fulfill({
        json: {
          providers: [
            { name: 'openai', models: ['gpt-4', 'gpt-3.5-turbo'] },
          ],
          default_provider: 'openai',
        },
      })
    );

    // Mock health
    await page.route('**/health', route =>
      route.fulfill({ json: { status: 'ok' } })
    );

    // Mock saved games
    await page.route('**/api/games', route =>
      route.fulfill({ json: { games: [] } })
    );

    // Mock usage stats
    await page.route('**/api/usage-stats*', route =>
      route.fulfill({ json: { hands_played: 3, hands_limit: 20 } })
    );

    // Mock career stats
    await page.route('**/api/career-stats*', route =>
      route.fulfill({ json: { games_played: 5, games_won: 2, win_rate: 0.4, total_knockouts: 3 } })
    );

    // Set localStorage for registered user, then navigate
    await page.goto('/game/new/custom', { waitUntil: 'commit' });
    await page.evaluate(() => {
      localStorage.setItem('currentUser', JSON.stringify({
        id: 'user-456',
        name: 'TestPlayer',
        is_guest: false,
        created_at: '2024-01-01',
        permissions: ['play', 'custom_game', 'themed_game'],
      }));
    });
    await page.goto('/game/new/custom');

    // Navigate through step 0 to reach step 1
    await navigateToStep1(page);
  });

  test('preset cards are visible: Quick & Dirty, Tournament, Deep Stack', async ({ page }) => {
    const presetCards = page.locator('.preset-card');
    await expect(presetCards).toHaveCount(3);

    await expect(presetCards.nth(0)).toContainText('Quick & Dirty');
    await expect(presetCards.nth(1)).toContainText('Tournament');
    await expect(presetCards.nth(2)).toContainText('Deep Stack');
  });

  test('Tournament preset is selected by default', async ({ page }) => {
    const tournamentCard = page.locator('.preset-card').nth(1);
    // The selectable-card--selected class is on the button element itself
    await expect(tournamentCard).toHaveClass(/selectable-card--selected/);
  });

  test('clicking a preset card selects it', async ({ page }) => {
    // Click Quick & Dirty
    const quickCard = page.locator('.preset-card').nth(0);
    await quickCard.click();

    // Quick & Dirty should now be selected
    await expect(quickCard).toHaveClass(/selectable-card--selected/);

    // Tournament should no longer be selected
    const tournamentCard = page.locator('.preset-card').nth(1);
    await expect(tournamentCard).not.toHaveClass(/selectable-card--selected/);
  });

  test('game mode cards are visible: Casual, Standard, Competitive, Pro', async ({ page }) => {
    const gameModeCards = page.locator('.game-mode-card');
    await expect(gameModeCards).toHaveCount(4);

    await expect(gameModeCards.nth(0)).toContainText('Casual');
    await expect(gameModeCards.nth(1)).toContainText('Standard');
    await expect(gameModeCards.nth(2)).toContainText('Competitive');
    await expect(gameModeCards.nth(3)).toContainText('Pro');
  });

  test('clicking a game mode card selects it', async ({ page }) => {
    const casualCard = page.locator('.game-mode-card').nth(0);
    await casualCard.click();

    await expect(casualCard).toHaveClass(/selectable-card--selected/);
  });

  test('advanced settings toggle expands and collapses', async ({ page }) => {
    // Advanced panel should not be visible initially
    await expect(page.locator('.advanced-panel')).not.toBeVisible();

    // Click the advanced toggle
    await page.locator('.advanced-toggle').click();

    // Advanced panel should now be visible
    await expect(page.locator('.advanced-panel')).toBeVisible();

    // Settings sections should be visible inside the panel
    await expect(page.locator('.settings-section')).toHaveCount(2);

    // Click again to collapse
    await page.locator('.advanced-toggle').click();

    // Advanced panel should be hidden again
    await expect(page.locator('.advanced-panel')).not.toBeVisible();
  });

  test('advanced panel shows game settings and model settings', async ({ page }) => {
    // Open advanced settings
    await page.locator('.advanced-toggle').click();

    // Game Settings section
    await expect(page.locator('.settings-section').nth(0)).toContainText('Game Settings');
    await expect(page.locator('.setting-label').filter({ hasText: 'Starting Stack' })).toBeVisible();
    await expect(page.locator('.setting-label').filter({ hasText: 'Big Blind' })).toBeVisible();
    await expect(page.locator('.setting-label').filter({ hasText: 'Blinds Increase' })).toBeVisible();

    // Model Settings section
    await expect(page.locator('.settings-section').nth(1)).toContainText('Default Model');
    await expect(page.locator('.setting-label').filter({ hasText: 'Provider' })).toBeVisible();
    await expect(page.locator('.setting-label').filter({ hasText: 'Model' })).toBeVisible();
  });

  test('Back button returns to step 0', async ({ page }) => {
    const backBtn = page.locator('.wizard-nav__btn--back');
    await expect(backBtn).toBeVisible();

    await backBtn.click();

    // Step 0 (Opponents) should now be active
    await expect(page.locator('.wizard-step').nth(0)).toHaveClass(/wizard-step--active/);

    // Player count buttons should be visible (step 0 content)
    await expect(page.locator('.player-count__btn').first()).toBeVisible();
  });

  test('Next button advances to step 2 (Review)', async ({ page }) => {
    const nextBtn = page.locator('.wizard-nav__btn--next');
    await expect(nextBtn).toBeVisible();
    await expect(nextBtn).toContainText('Next');

    await nextBtn.click();

    // Step 2 (Review) should now be active
    await expect(page.locator('.wizard-step').nth(2)).toHaveClass(/wizard-step--active/);
  });
});
