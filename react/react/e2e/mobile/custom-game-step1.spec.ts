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

async function navigateToStep1(page: import('@playwright/test').Page) {
  await page.locator('.fill-btn--random').click();
  await expect(page.locator('.player-card')).toHaveCount(3);

  await page.locator('.wizard-nav__btn--next').click();

  await expect(page.locator('.wizard-step').nth(1)).toHaveClass(/wizard-step--active/);
}

test.describe('PW-19: Custom game wizard step 1 — game settings on mobile', () => {
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
    await expect(tournamentCard).toHaveClass(/selectable-card--selected/);
  });

  test('clicking a preset card selects it', async ({ page }) => {
    const quickCard = page.locator('.preset-card').nth(0);
    await quickCard.click();

    await expect(quickCard).toHaveClass(/selectable-card--selected/);

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
    await expect(page.locator('.advanced-panel')).not.toBeVisible();

    await page.locator('.advanced-toggle').click();

    await expect(page.locator('.advanced-panel')).toBeVisible();

    await expect(page.locator('.settings-section')).toHaveCount(2);

    await page.locator('.advanced-toggle').click();

    await expect(page.locator('.advanced-panel')).not.toBeVisible();
  });

  test('advanced panel shows game settings and model settings', async ({ page }) => {
    await page.locator('.advanced-toggle').click();

    await expect(page.locator('.settings-section').nth(0)).toContainText('Game Settings');
    await expect(page.locator('.setting-label').filter({ hasText: 'Starting Stack' })).toBeVisible();
    await expect(page.locator('.setting-label').filter({ hasText: 'Big Blind' })).toBeVisible();
    await expect(page.locator('.setting-label').filter({ hasText: 'Blinds Increase' })).toBeVisible();

    await expect(page.locator('.settings-section').nth(1)).toContainText('Default Model');
    await expect(page.locator('.setting-label').filter({ hasText: 'Provider' })).toBeVisible();
    await expect(page.locator('.setting-label').filter({ hasText: 'Model' })).toBeVisible();
  });

  test('Back button returns to step 0', async ({ page }) => {
    const backBtn = page.locator('.wizard-nav__btn--back');
    await expect(backBtn).toBeVisible();

    await backBtn.click();

    await expect(page.locator('.wizard-step').nth(0)).toHaveClass(/wizard-step--active/);

    await expect(page.locator('.player-count__btn').first()).toBeVisible();
  });

  test('Next button advances to step 2 (Review)', async ({ page }) => {
    const nextBtn = page.locator('.wizard-nav__btn--next');
    await expect(nextBtn).toBeVisible();
    await expect(nextBtn).toContainText('Next');

    await nextBtn.click();

    await expect(page.locator('.wizard-step').nth(2)).toHaveClass(/wizard-step--active/);
  });
});
