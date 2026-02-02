import { test, expect } from '@playwright/test';
import { mockGamePageRoutes, navigateToGamePage, buildGameState } from '../helpers';

const initialGameState = buildGameState();

/** Showdown winner: Batman wins — human (TestPlayer) lost → show salty/gracious tones */
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

/** Fold winner: TestPlayer wins — human won → show gloat/humble tones */
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
  opts: {
    winnerPayload?: Record<string, unknown>;
    postRoundSuggestions?: { text: string; tone: string }[];
  } = {}
) {
  const winnerPayload = opts.winnerPayload || showdownWinnerInfo;
  const postRoundSuggestions = opts.postRoundSuggestions || [
    { text: 'Nice hand!', tone: 'humble' },
    { text: 'Got lucky there.', tone: 'humble' },
  ];

  const ctx = await mockGamePageRoutes(page, {
    gameState: initialGameState,
    socketEvents: [
      ['update_game_state', { game_state: initialGameState }],
      ['winner_announcement', winnerPayload],
    ],
  });

  // Override post-round chat with custom suggestions
  await page.route('**/api/game/*/post-round-chat*', route =>
    route.fulfill({ json: { suggestions: postRoundSuggestions } })
  );

  await navigateToGamePage(page, { mockContext: ctx });
}

test.describe('PW-12: Post-round chat — tone selection and suggestion sending', () => {

  test('loser sees Salty and Gracious tone buttons after winner announcement', async ({ page }) => {
    await setupWithWinner(page, { winnerPayload: showdownWinnerInfo });

    const overlay = page.getByTestId('winner-overlay');
    await expect(overlay).toBeVisible({ timeout: 15000 });

    const postRoundChat = overlay.locator('.post-round-chat');
    await expect(postRoundChat).toBeVisible({ timeout: 5000 });

    const toneButtons = overlay.locator('.post-round-tone');
    await expect(toneButtons).toHaveCount(2);

    const saltyBtn = overlay.locator('.tone-salty');
    await expect(saltyBtn).toBeVisible();
    const saltyText = await saltyBtn.textContent();
    expect(saltyText).toContain('Salty');

    const graciousBtn = overlay.locator('.tone-gracious');
    await expect(graciousBtn).toBeVisible();
    const graciousText = await graciousBtn.textContent();
    expect(graciousText).toContain('Gracious');
  });

  test('winner sees Gloat and Humble tone buttons', async ({ page }) => {
    await setupWithWinner(page, { winnerPayload: foldWinnerInfo });

    const overlay = page.getByTestId('winner-overlay');
    await expect(overlay).toBeVisible({ timeout: 15000 });

    const postRoundChat = overlay.locator('.post-round-chat');
    await expect(postRoundChat).toBeVisible({ timeout: 5000 });

    const gloatBtn = overlay.locator('.tone-gloat');
    await expect(gloatBtn).toBeVisible();
    const gloatText = await gloatBtn.textContent();
    expect(gloatText).toContain('Gloat');

    const humbleBtn = overlay.locator('.tone-humble');
    await expect(humbleBtn).toBeVisible();
    const humbleText = await humbleBtn.textContent();
    expect(humbleText).toContain('Humble');
  });

  test('selecting a tone shows loading then suggestions', async ({ page }) => {
    await setupWithWinner(page, {
      winnerPayload: showdownWinnerInfo,
      postRoundSuggestions: [
        { text: 'Nice hand, well played!', tone: 'gracious' },
        { text: 'You earned that one.', tone: 'gracious' },
      ],
    });

    const overlay = page.getByTestId('winner-overlay');
    await expect(overlay).toBeVisible({ timeout: 15000 });

    const graciousBtn = overlay.locator('.tone-gracious');
    await expect(graciousBtn).toBeVisible({ timeout: 5000 });
    await graciousBtn.click({ force: true });

    const suggestions = overlay.locator('.post-round-suggestion');
    await expect(suggestions.first()).toBeVisible({ timeout: 10000 });

    await expect(suggestions).toHaveCount(2);

    const firstSuggestion = suggestions.nth(0);
    await expect(firstSuggestion).toContainText('Nice hand, well played!');

    const secondSuggestion = suggestions.nth(1);
    await expect(secondSuggestion).toContainText('You earned that one.');
  });

  test('tapping a suggestion shows Sent confirmation', async ({ page }) => {
    await setupWithWinner(page, {
      winnerPayload: showdownWinnerInfo,
      postRoundSuggestions: [
        { text: 'Nice hand!', tone: 'gracious' },
        { text: 'Well played.', tone: 'gracious' },
      ],
    });

    const overlay = page.getByTestId('winner-overlay');
    await expect(overlay).toBeVisible({ timeout: 15000 });

    const graciousBtn = overlay.locator('.tone-gracious');
    await expect(graciousBtn).toBeVisible({ timeout: 5000 });
    await graciousBtn.click({ force: true });

    const suggestions = overlay.locator('.post-round-suggestion');
    await expect(suggestions.first()).toBeVisible({ timeout: 10000 });

    await suggestions.first().click();

    const sentConfirmation = overlay.locator('.post-round-sent');
    await expect(sentConfirmation).toBeVisible({ timeout: 5000 });
    const sentText = await sentConfirmation.textContent();
    expect(sentText).toContain('Sent');
  });

  test('Back button returns to tone selection', async ({ page }) => {
    await setupWithWinner(page, {
      winnerPayload: showdownWinnerInfo,
      postRoundSuggestions: [
        { text: 'Nice hand!', tone: 'gracious' },
        { text: 'Well played.', tone: 'gracious' },
      ],
    });

    const overlay = page.getByTestId('winner-overlay');
    await expect(overlay).toBeVisible({ timeout: 15000 });

    const graciousBtn = overlay.locator('.tone-gracious');
    await expect(graciousBtn).toBeVisible({ timeout: 5000 });
    await graciousBtn.click({ force: true });

    const suggestions = overlay.locator('.post-round-suggestion');
    await expect(suggestions.first()).toBeVisible({ timeout: 10000 });

    const backBtn = overlay.locator('.post-round-back');
    await expect(backBtn).toBeVisible();
    await backBtn.click();

    const toneButtons = overlay.locator('.post-round-tones');
    await expect(toneButtons).toBeVisible({ timeout: 5000 });

    await expect(overlay.locator('.tone-salty')).toBeVisible();
    await expect(overlay.locator('.tone-gracious')).toBeVisible();
  });

});
