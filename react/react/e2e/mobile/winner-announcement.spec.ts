import { test, expect } from '@playwright/test';
import { mockGamePageRoutes, navigateToGamePage, buildGameState } from '../helpers';

const initialGameState = buildGameState();

/** Showdown winner_info fixture: Batman wins with Pair of Kings */
const showdownWinnerInfo = {
  winners: ['Batman'],
  hand_name: 'Pair of Kings',
  showdown: true,
  pot_breakdown: [
    {
      pot_name: 'Main Pot',
      total_amount: 300,
      winners: [{ name: 'Batman', amount: 300 }],
      hand_name: 'Pair of Kings',
    },
  ],
  players_showdown: {
    Batman: {
      cards: [
        { rank: 'K', suit: 'spades' },
        { rank: 'K', suit: 'hearts' },
      ],
      hand_name: 'Pair of Kings',
      hand_rank: 3,
      kickers: ['A', 'Q'],
    },
    TestPlayer: {
      cards: [
        { rank: 'A', suit: 'spades' },
        { rank: 'K', suit: 'hearts' },
      ],
      hand_name: 'Ace High',
      hand_rank: 9,
      kickers: ['K', 'Q', 'J'],
    },
  },
  community_cards: [
    { rank: '7', suit: 'diamonds' },
    { rank: 'Q', suit: 'clubs' },
    { rank: '3', suit: 'hearts' },
    { rank: '9', suit: 'spades' },
    { rank: '2', suit: 'diamonds' },
  ],
  is_final_hand: false,
};

/** Fold winner_info fixture: TestPlayer wins because all opponents folded */
const foldWinnerInfo = {
  winners: ['TestPlayer'],
  hand_name: undefined,
  showdown: false,
  pot_breakdown: [
    {
      pot_name: 'Main Pot',
      total_amount: 150,
      winners: [{ name: 'TestPlayer', amount: 150 }],
      hand_name: '',
    },
  ],
  is_final_hand: false,
};

async function setupWithWinner(
  page: import('@playwright/test').Page,
  winnerPayload: Record<string, unknown>,
  opts: { isGuest?: boolean } = {}
) {
  const ctx = await mockGamePageRoutes(page, {
    isGuest: opts.isGuest,
    gameState: initialGameState,
    socketEvents: [
      ['update_game_state', { game_state: initialGameState }],
      ['winner_announcement', winnerPayload],
    ],
  });
  await navigateToGamePage(page, { isGuest: opts.isGuest, mockContext: ctx });
}

test.describe('PW-11: Winner announcement shows after hand and auto-dismisses', () => {

  test('showdown winner overlay appears with winner name and amount', async ({ page }) => {
    await setupWithWinner(page, showdownWinnerInfo);

    const overlay = page.locator('.mobile-winner-overlay');
    await expect(overlay).toBeVisible({ timeout: 15000 });

    const winnerNames = overlay.locator('.winner-names');
    await expect(winnerNames).toBeVisible();
    const nameText = await winnerNames.textContent();
    expect(nameText).toContain('Batman');
    expect(nameText).toContain('wins');

    const winnerAmount = overlay.locator('.winner-amount');
    await expect(winnerAmount).toBeVisible();
    const amountText = await winnerAmount.textContent();
    expect(amountText).toContain('300');

    const handName = overlay.locator('.winner-hand-name');
    await expect(handName).toBeVisible();
    const handText = await handName.textContent();
    expect(handText).toContain('Pair of Kings');
  });

  test('showdown displays community cards and player hands', async ({ page }) => {
    await setupWithWinner(page, showdownWinnerInfo);

    const overlay = page.locator('.mobile-winner-overlay');
    await expect(overlay).toBeVisible({ timeout: 15000 });

    const showdownSection = overlay.locator('.showdown-section');
    await expect(showdownSection).toBeVisible({ timeout: 5000 });

    const communitySection = overlay.locator('.community-section');
    await expect(communitySection).toBeVisible();

    const playerShowdowns = overlay.locator('.player-showdown');
    const count = await playerShowdowns.count();
    expect(count).toBe(2);

    const winnerHand = overlay.locator('.player-showdown.winner');
    await expect(winnerHand).toBeVisible();
  });

  test('showdown has Continue button', async ({ page }) => {
    await setupWithWinner(page, showdownWinnerInfo);

    const overlay = page.locator('.mobile-winner-overlay');
    await expect(overlay).toBeVisible({ timeout: 15000 });

    const dismissBtn = overlay.locator('.dismiss-btn');
    await expect(dismissBtn).toBeVisible();
    const btnText = await dismissBtn.textContent();
    expect(btnText).toContain('Continue');
  });

  test('fold winner shows name, amount, and "All opponents folded"', async ({ page }) => {
    await setupWithWinner(page, foldWinnerInfo);

    const overlay = page.locator('.mobile-winner-overlay');
    await expect(overlay).toBeVisible({ timeout: 15000 });

    const showdownSection = overlay.locator('.showdown-section');
    await expect(showdownSection).not.toBeVisible();

    const noShowdown = overlay.locator('.no-showdown-winner');
    await expect(noShowdown).toBeVisible();

    const noShowdownName = overlay.locator('.no-showdown-name');
    await expect(noShowdownName).toBeVisible();
    const nameText = await noShowdownName.textContent();
    expect(nameText).toContain('TestPlayer');

    const noShowdownAmount = overlay.locator('.no-showdown-amount');
    await expect(noShowdownAmount).toBeVisible();
    const amountText = await noShowdownAmount.textContent();
    expect(amountText).toContain('150');

    const foldedText = overlay.locator('.no-showdown-text');
    await expect(foldedText).toBeVisible();
    const text = await foldedText.textContent();
    expect(text).toContain('All opponents folded');
  });

  test('fold winner has no showdown cards section', async ({ page }) => {
    await setupWithWinner(page, foldWinnerInfo);

    const overlay = page.locator('.mobile-winner-overlay');
    await expect(overlay).toBeVisible({ timeout: 15000 });

    const showdownSection = overlay.locator('.showdown-section');
    await expect(showdownSection).not.toBeVisible();

    const dismissBtn = overlay.locator('.dismiss-btn');
    await expect(dismissBtn).toBeVisible();
  });

  test('clicking Continue dismisses the winner overlay', async ({ page }) => {
    await setupWithWinner(page, foldWinnerInfo);

    const overlay = page.locator('.mobile-winner-overlay');
    await expect(overlay).toBeVisible({ timeout: 15000 });

    const dismissBtn = overlay.locator('.dismiss-btn');
    await dismissBtn.click();

    await expect(overlay).not.toBeVisible({ timeout: 5000 });
  });

});
