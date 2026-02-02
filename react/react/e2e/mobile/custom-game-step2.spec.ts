import { test, expect } from '@playwright/test';
import { mockMenuPageRoutes, navigateToMenuPage } from '../helpers';

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
    await mockMenuPageRoutes(page, {
      isGuest: false,
      personalities: personalitiesResponse,
      userModels: {
        providers: [
          { name: 'openai', models: ['gpt-4', 'gpt-3.5-turbo'] },
        ],
        default_provider: 'openai',
      },
      includeAvatar: true,
    });
    await navigateToMenuPage(page, { isGuest: false, path: '/game/new/custom' });

    await navigateToStep2(page);
  });

  test('step indicator shows step 3 (Review) active', async ({ page }) => {
    const steps = page.locator('.wizard-step');
    await expect(steps).toHaveCount(3);

    await expect(steps.nth(2)).toHaveClass(/wizard-step--active/);
    await expect(steps.nth(2).locator('.wizard-step__label')).toContainText('Review');
  });

  test('review section shows selected opponents in fan layout', async ({ page }) => {
    const reviewBlocks = page.locator('.review-block');
    await expect(reviewBlocks).toHaveCount(2);

    await expect(reviewBlocks.nth(0)).toContainText('Opponents');

    const fanCards = page.locator('.review-fan__card');
    await expect(fanCards).toHaveCount(3);

    const names = page.locator('.review-fan__name');
    await expect(names).toHaveCount(3);
  });

  test('review section shows game settings summary', async ({ page }) => {
    const reviewBlocks = page.locator('.review-block');

    await expect(reviewBlocks.nth(1)).toContainText('Settings');

    const stats = page.locator('.review-stat');
    await expect(stats).toHaveCount(4);

    await expect(page.locator('.review-stat__label').nth(0)).toContainText('Stack');
    await expect(page.locator('.review-stat__label').nth(1)).toContainText('BB');
    await expect(page.locator('.review-stat__label').nth(2)).toContainText('Mode');
    await expect(page.locator('.review-stat__label').nth(3)).toContainText('AI');
  });

  test('opponents review block has Edit button that returns to step 0', async ({ page }) => {
    const editBtns = page.locator('.review-block__edit');
    await expect(editBtns).toHaveCount(2);

    await editBtns.nth(0).click();

    await expect(page.locator('.wizard-step').nth(0)).toHaveClass(/wizard-step--active/);
    await expect(page.locator('.player-count__btn').first()).toBeVisible();
  });

  test('settings review block has Edit button that returns to step 1', async ({ page }) => {
    const editBtns = page.locator('.review-block__edit');

    await editBtns.nth(1).click();

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
    await page.route('**/api/new-game', route =>
      route.fulfill({ json: { game_id: 'test-game-123' } })
    );

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

    await page.locator('.wizard-nav__btn--next.wizard-nav__btn--full').click();

    await page.waitForURL(/\/game\/test-game-123/);
    await expect(page).toHaveURL(/\/game\/test-game-123/);
  });
});
