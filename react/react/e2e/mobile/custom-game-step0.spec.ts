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

test.describe('PW-18: Custom game wizard step 0 — choose opponents on mobile', () => {
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
    });
    await navigateToMenuPage(page, { isGuest: false, path: '/game/new/custom' });
  });

  test('step indicator shows step 1 (Opponents) active', async ({ page }) => {
    const steps = page.locator('.wizard-step');
    await expect(steps).toHaveCount(3);

    await expect(steps.nth(0)).toHaveClass(/wizard-step--active/);

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

    await expect(buttons.nth(2)).toHaveClass(/player-count__btn--selected/);
  });

  test('"Fill Randomly" button is visible and clickable', async ({ page }) => {
    const fillBtn = page.locator('.fill-btn--random');
    await expect(fillBtn).toBeVisible();
    await expect(fillBtn).toContainText('Fill Randomly');

    await fillBtn.click();

    const playerCards = page.locator('.player-card');
    await expect(playerCards).toHaveCount(3);
  });

  test('clicking a player count updates the number of slots', async ({ page }) => {
    await page.locator('.player-count__btn').nth(1).click();
    await expect(page.locator('.player-count__btn').nth(1)).toHaveClass(/player-count__btn--selected/);

    const emptySlots = page.locator('.player-slot--empty');
    await expect(emptySlots).toHaveCount(2);

    await page.locator('.player-count__btn').nth(4).click();
    await expect(page.locator('.player-count__btn').nth(4)).toHaveClass(/player-count__btn--selected/);

    await expect(page.locator('.player-slot--empty')).toHaveCount(5);
  });

  test('empty slots show placeholder text', async ({ page }) => {
    const emptySlot = page.locator('.player-slot--empty').first();
    await expect(emptySlot).toBeVisible();
    await expect(emptySlot.locator('.player-slot__empty-label')).toContainText('Empty Seat');
  });

  test('"Next" button is present and disabled when no opponents selected', async ({ page }) => {
    const nextBtn = page.locator('.wizard-nav__btn--next');
    await expect(nextBtn).toBeVisible();
    await expect(nextBtn).toContainText('Next');
    await expect(nextBtn).toBeDisabled();
  });

  test('"Next" button becomes enabled after filling opponents', async ({ page }) => {
    await page.locator('.fill-btn--random').click();
    await expect(page.locator('.player-card')).toHaveCount(3);

    const nextBtn = page.locator('.wizard-nav__btn--next');
    await expect(nextBtn).toBeEnabled();
  });

  test('clicking "Next" advances to step 1 (Settings)', async ({ page }) => {
    await page.locator('.fill-btn--random').click();
    await expect(page.locator('.player-card')).toHaveCount(3);

    await page.locator('.wizard-nav__btn--next').click();

    const steps = page.locator('.wizard-step');
    await expect(steps.nth(1)).toHaveClass(/wizard-step--active/);

    await expect(page.locator('.preset-card').first()).toBeVisible();
  });

  test('clicking empty slot opens personality picker', async ({ page }) => {
    await page.locator('.player-slot--empty').first().click();

    await expect(page.locator('.personality-picker__search-input')).toBeVisible();

    const items = page.locator('.personality-picker__item');
    await expect(items.first()).toBeVisible();
  });

  test('selecting a personality from picker fills the slot', async ({ page }) => {
    await page.locator('.player-slot--empty').first().click();

    await page.locator('.personality-picker__item').first().click();

    await expect(page.locator('.player-card')).toHaveCount(1);

    await expect(page.locator('.player-slot--empty')).toHaveCount(2);
  });
});
