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
 * Navigate through step 0 (fill opponents) and step 1 (settings) to reach step 2 (review).
 */
async function navigateToStep2(page: import('@playwright/test').Page) {
  // Step 0: Fill opponents randomly
  await page.locator('.fill-btn--random').click();
  await expect(page.locator('.player-card')).toHaveCount(3);

  // Advance to step 1
  await page.locator('.wizard-nav__btn--next').click();
  await expect(page.locator('.wizard-step').nth(1)).toHaveClass(/wizard-step--active/);

  // Step 1: Just click Next to advance to step 2 (defaults are fine)
  await page.locator('.wizard-nav__btn--next').click();
  await expect(page.locator('.wizard-step').nth(2)).toHaveClass(/wizard-step--active/);
}

test.describe('PW-20: Custom game wizard step 2 — review and create on mobile', () => {
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

    // Mock avatar endpoint
    await page.route('**/api/avatar/**', route =>
      route.fulfill({
        contentType: 'image/png',
        body: Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==', 'base64'),
      })
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

    // Navigate through steps 0 and 1 to reach step 2
    await navigateToStep2(page);
  });

  test('step indicator shows step 3 (Review) active', async ({ page }) => {
    const steps = page.locator('.wizard-step');
    await expect(steps).toHaveCount(3);

    // Third step should be active
    await expect(steps.nth(2)).toHaveClass(/wizard-step--active/);
    await expect(steps.nth(2).locator('.wizard-step__label')).toContainText('Review');
  });

  test('review section shows selected opponents in fan layout', async ({ page }) => {
    // Review blocks should be present
    const reviewBlocks = page.locator('.review-block');
    await expect(reviewBlocks).toHaveCount(2);

    // First block is Opponents
    await expect(reviewBlocks.nth(0)).toContainText('Opponents');

    // Fan layout should show the 3 randomly filled opponents
    const fanCards = page.locator('.review-fan__card');
    await expect(fanCards).toHaveCount(3);

    // Each card should have a name
    const names = page.locator('.review-fan__name');
    await expect(names).toHaveCount(3);
  });

  test('review section shows game settings summary', async ({ page }) => {
    const reviewBlocks = page.locator('.review-block');

    // Second block is Settings
    await expect(reviewBlocks.nth(1)).toContainText('Settings');

    // Settings strip should show 4 stats
    const stats = page.locator('.review-stat');
    await expect(stats).toHaveCount(4);

    // Check stat labels
    await expect(page.locator('.review-stat__label').nth(0)).toContainText('Stack');
    await expect(page.locator('.review-stat__label').nth(1)).toContainText('BB');
    await expect(page.locator('.review-stat__label').nth(2)).toContainText('Mode');
    await expect(page.locator('.review-stat__label').nth(3)).toContainText('AI');
  });

  test('opponents review block has Edit button that returns to step 0', async ({ page }) => {
    const editBtns = page.locator('.review-block__edit');
    await expect(editBtns).toHaveCount(2);

    // Click first Edit (Opponents)
    await editBtns.nth(0).click();

    // Should return to step 0
    await expect(page.locator('.wizard-step').nth(0)).toHaveClass(/wizard-step--active/);
    await expect(page.locator('.player-count__btn').first()).toBeVisible();
  });

  test('settings review block has Edit button that returns to step 1', async ({ page }) => {
    const editBtns = page.locator('.review-block__edit');

    // Click second Edit (Settings)
    await editBtns.nth(1).click();

    // Should return to step 1
    await expect(page.locator('.wizard-step').nth(1)).toHaveClass(/wizard-step--active/);
    await expect(page.locator('.preset-card').first()).toBeVisible();
  });

  test('"Deal Me In" button is visible', async ({ page }) => {
    const dealBtn = page.locator('.wizard-nav__btn--next.wizard-nav__btn--full');
    await expect(dealBtn).toBeVisible();
    await expect(dealBtn).toContainText('Deal Me In');
    await expect(dealBtn).toBeEnabled();
  });

  test('clicking "Deal Me In" creates game and navigates to game page', async ({ page }) => {
    // Mock new-game endpoint to return a game ID
    await page.route('**/api/new-game', route =>
      route.fulfill({ json: { game_id: 'test-game-123' } })
    );

    // Mock game state endpoint for the created game
    await page.route('**/api/game/test-game-123/state', route =>
      route.fulfill({
        json: {
          game_id: 'test-game-123',
          phase: 'PRE_FLOP',
          community_cards: [],
          pot: { total: 150, main: 150, side_pots: [] },
          current_player_idx: 0,
          dealer_idx: 2,
          small_blind: 25,
          big_blind: 50,
          hand_number: 1,
          players: [
            { name: 'TestPlayer', stack: 1950, bet: 50, is_human: true, is_folded: false, is_all_in: false, hand: [{ rank: 'A', suit: 'spades' }, { rank: 'K', suit: 'hearts' }], player_options: ['fold', 'call', 'raise'], is_dealer: false, personality: null, avatar_url: null, nickname: null, psychology: null, llm_debug_info: null },
            { name: 'Batman', stack: 1975, bet: 25, is_human: false, is_folded: false, is_all_in: false, hand: null, player_options: [], is_dealer: false, personality: { name: 'Batman', play_style: 'Tight-Aggressive' }, avatar_url: '/avatars/batman.png', nickname: 'The Dark Knight', psychology: { tilt_level: 0.0, tilt_category: 'none' }, llm_debug_info: null },
            { name: 'Gandalf', stack: 2000, bet: 0, is_human: false, is_folded: false, is_all_in: false, hand: null, player_options: [], is_dealer: true, personality: { name: 'Gandalf', play_style: 'Calculated' }, avatar_url: '/avatars/gandalf.png', nickname: 'The Grey', psychology: { tilt_level: 0.2, tilt_category: 'mild' }, llm_debug_info: null },
          ],
          betting_context: { min_raise_to: 100, max_raise_to: 2000, cost_to_call: 0, current_bet: 50, highest_bet: 50 },
          messages: [],
          is_hand_complete: false,
          is_game_over: false,
          winner_info: null,
          tournament_result: null,
        },
      })
    );

    // Mock socket.io polling
    await page.route('**/socket.io/**', route => {
      const url = route.request().url();
      if (url.includes('transport=polling') && route.request().method() === 'GET') {
        if (!url.includes('sid=')) {
          route.fulfill({
            contentType: 'text/plain',
            body: '0{"sid":"fake-sid","upgrades":[],"pingInterval":25000,"pingTimeout":20000}',
          });
        } else {
          route.fulfill({ contentType: 'text/plain', body: '2' });
        }
      } else {
        route.fulfill({ body: 'ok' });
      }
    });

    // Click "Deal Me In"
    await page.locator('.wizard-nav__btn--next.wizard-nav__btn--full').click();

    // Should navigate to the game page
    await page.waitForURL(/\/game\/test-game-123/);
    await expect(page).toHaveURL(/\/game\/test-game-123/);
  });
});
