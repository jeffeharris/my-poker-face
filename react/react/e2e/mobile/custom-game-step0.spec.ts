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

test.describe('PW-18: Custom game wizard step 0 — choose opponents on mobile', () => {
  test.beforeEach(async ({ page }) => {
    // Mock auth/me to return registered user (custom game requires registration)
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

    // Mock user-models endpoint for LLM provider config
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
  });

  test('step indicator shows step 1 (Opponents) active', async ({ page }) => {
    const steps = page.locator('.wizard-step');
    await expect(steps).toHaveCount(3);

    // First step should be active
    await expect(steps.nth(0)).toHaveClass(/wizard-step--active/);

    // Step labels
    await expect(steps.nth(0).locator('.wizard-step__label')).toContainText('Opponents');
    await expect(steps.nth(1).locator('.wizard-step__label')).toContainText('Settings');
    await expect(steps.nth(2).locator('.wizard-step__label')).toContainText('Review');
  });

  test('player count buttons (1-5) are visible', async ({ page }) => {
    const buttons = page.locator('.player-count__btn');
    await expect(buttons).toHaveCount(5);

    await expect(buttons.nth(0)).toContainText('1');
    await expect(buttons.nth(1)).toContainText('2');
    await expect(buttons.nth(2)).toContainText('3');
    await expect(buttons.nth(3)).toContainText('4');
    await expect(buttons.nth(4)).toContainText('5');

    // Default selection is 3
    await expect(buttons.nth(2)).toHaveClass(/player-count__btn--selected/);
  });

  test('"Fill Randomly" button is visible and clickable', async ({ page }) => {
    const fillBtn = page.locator('.fill-btn--random');
    await expect(fillBtn).toBeVisible();
    await expect(fillBtn).toContainText('Fill Randomly');

    // Click fills the empty slots
    await fillBtn.click();

    // After fill, empty slots should be replaced with player cards
    const playerCards = page.locator('.player-card');
    await expect(playerCards).toHaveCount(3);
  });

  test('clicking a player count updates the number of slots', async ({ page }) => {
    // Click 2 opponents
    await page.locator('.player-count__btn').nth(1).click();
    await expect(page.locator('.player-count__btn').nth(1)).toHaveClass(/player-count__btn--selected/);

    // Should show 2 empty slots
    const emptySlots = page.locator('.player-slot--empty');
    await expect(emptySlots).toHaveCount(2);

    // Click 5 opponents
    await page.locator('.player-count__btn').nth(4).click();
    await expect(page.locator('.player-count__btn').nth(4)).toHaveClass(/player-count__btn--selected/);

    // Should show 5 empty slots
    await expect(page.locator('.player-slot--empty')).toHaveCount(5);
  });

  test('empty slots show placeholder text', async ({ page }) => {
    const emptySlot = page.locator('.player-slot--empty').first();
    await expect(emptySlot).toBeVisible();
    await expect(emptySlot.locator('.player-slot__empty-label')).toContainText('Empty Seat');
  });

  test('"Next" button is present and disabled when no opponents selected', async ({ page }) => {
    // Next button should be visible but disabled when no opponents are filled
    const nextBtn = page.locator('.wizard-nav__btn--next');
    await expect(nextBtn).toBeVisible();
    await expect(nextBtn).toContainText('Next');
    await expect(nextBtn).toBeDisabled();
  });

  test('"Next" button becomes enabled after filling opponents', async ({ page }) => {
    // Fill randomly
    await page.locator('.fill-btn--random').click();
    await expect(page.locator('.player-card')).toHaveCount(3);

    // Next button should now be enabled
    const nextBtn = page.locator('.wizard-nav__btn--next');
    await expect(nextBtn).toBeEnabled();
  });

  test('clicking "Next" advances to step 1 (Settings)', async ({ page }) => {
    // Fill opponents first
    await page.locator('.fill-btn--random').click();
    await expect(page.locator('.player-card')).toHaveCount(3);

    // Click Next
    await page.locator('.wizard-nav__btn--next').click();

    // Step 2 (Settings) should now be active
    const steps = page.locator('.wizard-step');
    await expect(steps.nth(1)).toHaveClass(/wizard-step--active/);

    // Preset cards should be visible on the settings step
    await expect(page.locator('.preset-card').first()).toBeVisible();
  });

  test('clicking empty slot opens personality picker', async ({ page }) => {
    // Click the first empty slot
    await page.locator('.player-slot--empty').first().click();

    // Personality picker search input should appear
    await expect(page.locator('.personality-picker__search-input')).toBeVisible();

    // Personality list should show available personalities
    const items = page.locator('.personality-picker__item');
    await expect(items.first()).toBeVisible();
  });

  test('selecting a personality from picker fills the slot', async ({ page }) => {
    // Click the first empty slot
    await page.locator('.player-slot--empty').first().click();

    // Click the first personality item
    await page.locator('.personality-picker__item').first().click();

    // One slot should now be filled (a player card appeared)
    await expect(page.locator('.player-card')).toHaveCount(1);

    // Remaining slots should be empty
    await expect(page.locator('.player-slot--empty')).toHaveCount(2);
  });
});
