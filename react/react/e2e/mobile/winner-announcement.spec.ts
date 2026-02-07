import { test, expect } from '@playwright/test';
import { mockGamePageRoutes, navigateToGamePage, buildGameState } from '../helpers';

const initialGameState = buildGameState();
const handOverGameState = buildGameState([], { phase: 'HAND_OVER', player_options: [] });

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

    const overlay = page.getByTestId('winner-overlay');
    await expect(overlay).toBeVisible({ timeout: 15000 });

    const winnerNames = overlay.getByTestId('winner-names');
    await expect(winnerNames).toBeVisible();
    const nameText = await winnerNames.textContent();
    expect(nameText).toContain('Batman');
    expect(nameText).toContain('wins');

    const winnerAmount = overlay.getByTestId('winner-amount');
    await expect(winnerAmount).toBeVisible();
    const amountText = await winnerAmount.textContent();
    expect(amountText).toContain('300');

    const handName = overlay.getByTestId('winner-hand-name');
    await expect(handName).toBeVisible();
    const handText = await handName.textContent();
    expect(handText).toContain('Pair of Kings');
  });

  test('showdown displays community cards and player hands', async ({ page }) => {
    await setupWithWinner(page, showdownWinnerInfo);

    const overlay = page.getByTestId('winner-overlay');
    await expect(overlay).toBeVisible({ timeout: 15000 });

    const showdownSection = overlay.getByTestId('showdown-section');
    await expect(showdownSection).toBeVisible({ timeout: 5000 });

    const communitySection = overlay.getByTestId('community-section');
    await expect(communitySection).toBeVisible();

    const playerShowdowns = overlay.getByTestId('player-showdown');
    const count = await playerShowdowns.count();
    expect(count).toBe(2);

    const winnerHand = overlay.locator('.player-showdown.winner');
    await expect(winnerHand).toBeVisible({ timeout: 10000 });
  });

  test('showdown has Continue button', async ({ page }) => {
    await setupWithWinner(page, showdownWinnerInfo);

    const overlay = page.getByTestId('winner-overlay');
    await expect(overlay).toBeVisible({ timeout: 15000 });

    const dismissBtn = overlay.getByTestId('winner-dismiss');
    await expect(dismissBtn).toBeVisible();
    const btnText = await dismissBtn.textContent();
    expect(btnText).toContain('Continue');
  });

  test('fold winner does NOT show winner-overlay (handled by ShuffleLoading)', async ({ page }) => {
    const ctx = await mockGamePageRoutes(page, {
      gameState: initialGameState,
      socketEvents: [
        ['update_game_state', { game_state: initialGameState }],
        ['winner_announcement', foldWinnerInfo],
        ['update_game_state', { game_state: handOverGameState }],
      ],
    });
    await navigateToGamePage(page, { mockContext: ctx });

    // Target the interhand ShuffleLoading (uses .shuffle-loading-dim), not the
    // initial "Setting up the table" overlay which may still be fading out.
    const shuffle = page.locator('.shuffle-loading-dim');
    await expect(shuffle).toBeVisible({ timeout: 15000 });

    const overlay = page.getByTestId('winner-overlay');
    await expect(overlay).not.toBeVisible();
  });

  test('fold winner shows name and amount on ShuffleLoading', async ({ page }) => {
    const ctx = await mockGamePageRoutes(page, {
      gameState: initialGameState,
      socketEvents: [
        ['update_game_state', { game_state: initialGameState }],
        ['winner_announcement', foldWinnerInfo],
        ['update_game_state', { game_state: handOverGameState }],
      ],
    });
    await navigateToGamePage(page, { mockContext: ctx });

    const shuffle = page.locator('.shuffle-loading-dim');
    await expect(shuffle).toBeVisible({ timeout: 15000 });

    // Scope to the interhand ShuffleLoading's sibling content layer
    const contentLayer = page.locator('.shuffle-loading-content-layer');
    const messageText = contentLayer.locator('.shuffle-loading-text');
    await expect(messageText).toContainText('TestPlayer', { timeout: 10000 });
    await expect(messageText).toContainText('won');

    const submessage = contentLayer.locator('.shuffle-loading-submessage');
    await expect(submessage).toContainText('$150', { timeout: 10000 });
  });

  test('fold winner has no Continue button (auto-transitions)', async ({ page }) => {
    const ctx = await mockGamePageRoutes(page, {
      gameState: initialGameState,
      socketEvents: [
        ['update_game_state', { game_state: initialGameState }],
        ['winner_announcement', foldWinnerInfo],
        ['update_game_state', { game_state: handOverGameState }],
      ],
    });
    await navigateToGamePage(page, { mockContext: ctx });

    const shuffle = page.locator('.shuffle-loading-dim');
    await expect(shuffle).toBeVisible({ timeout: 15000 });

    const dismissBtn = page.getByTestId('winner-dismiss');
    await expect(dismissBtn).not.toBeVisible();
  });

});
