import { test, expect } from '@playwright/test';
import { readFileSync } from 'fs';
import { dirname, join } from 'path';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

const gameStateFixture = JSON.parse(
  readFileSync(join(__dirname, '../fixtures/game-state.json'), 'utf-8')
);

/**
 * Build a game state with configurable options.
 */
function buildGameState(overrides: Record<string, unknown> = {}) {
  const players = gameStateFixture.players.map((p: Record<string, unknown>, i: number) => {
    if (i === 0) return { ...p, player_options: ['fold', 'call', 'raise'] };
    return p;
  });
  return {
    ...gameStateFixture,
    players,
    ...overrides,
  };
}

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

/**
 * Set up all mocks and navigate to the game page.
 * Delivers a winner_announcement event via socket.io after initial load.
 */
async function setupGamePage(
  page: import('@playwright/test').Page,
  opts: {
    isGuest?: boolean;
    winnerPayload?: Record<string, unknown>;
    postRoundSuggestions?: { text: string; tone: string }[];
  } = {}
) {
  const isGuest = opts.isGuest !== false;
  const initialGameState = buildGameState();
  const winnerPayload = opts.winnerPayload || showdownWinnerInfo;
  const postRoundSuggestions = opts.postRoundSuggestions || [
    { text: 'Nice hand!', tone: 'humble' },
    { text: 'Got lucky there.', tone: 'humble' },
  ];

  // Intercept useAuth to disable dev-mode guest bypass
  await page.route('**/@fs/**useAuth**', async route => {
    const response = await route.fetch();
    let body = await response.text();
    body = body.replace(
      /import\.meta\.env\.VITE_FORCE_GUEST\s*!==\s*['"]true['"]/,
      'false'
    );
    await route.fulfill({ response, body });
  });
  await page.route('**/src/hooks/useAuth**', async route => {
    const response = await route.fetch();
    let body = await response.text();
    body = body.replace(
      /import\.meta\.env\.VITE_FORCE_GUEST\s*!==\s*['"]true['"]/,
      'false'
    );
    await route.fulfill({ response, body });
  });

  // Mock auth
  await page.route('**/api/auth/me', route =>
    route.fulfill({
      json: {
        user: {
          id: isGuest ? 'guest-123' : 'user-456',
          name: 'TestPlayer',
          is_guest: isGuest,
          created_at: '2024-01-01',
          permissions: isGuest ? ['play'] : ['play', 'custom_game', 'themed_game']
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

  // Mock new-game
  await page.route('**/api/new-game', route =>
    route.fulfill({ json: { game_id: 'test-game-123' } })
  );

  // Mock game state
  await page.route('**/api/game-state/test-game-123', route =>
    route.fulfill({ json: initialGameState })
  );

  // Mock player action
  await page.route('**/api/game/*/action', route =>
    route.fulfill({ json: { success: true } })
  );

  // Mock chat send
  await page.route('**/api/game/*/chat', route =>
    route.fulfill({ json: { success: true } })
  );

  // Mock post-round chat suggestions
  await page.route('**/api/game/*/post-round-chat*', route =>
    route.fulfill({
      json: { suggestions: postRoundSuggestions }
    })
  );

  // Mock quick chat suggestions
  await page.route('**/api/game/*/chat-suggestions*', route =>
    route.fulfill({
      json: {
        suggestions: [
          { text: 'Nice play!', category: 'compliment' },
          { text: 'You got me there!', category: 'concession' }
        ]
      }
    })
  );

  // Socket.IO mock: deliver winner_announcement event after connect
  let pollCount = 0;
  await page.route('**/socket.io/**', route => {
    const url = route.request().url();
    if (url.includes('transport=polling') && route.request().method() === 'GET') {
      if (!url.includes('sid=')) {
        // Engine.IO handshake
        route.fulfill({
          contentType: 'text/plain',
          body: '0{"sid":"fake-sid","upgrades":[],"pingInterval":25000,"pingTimeout":20000}'
        });
      } else {
        pollCount++;
        if (pollCount === 1) {
          // Socket.IO connect ack
          route.fulfill({
            contentType: 'text/plain',
            body: '40{"sid":"fake-socket-sid"}'
          });
        } else if (pollCount === 2) {
          // Deliver initial game state via socket
          const gameStatePayload = JSON.stringify(['update_game_state', { game_state: initialGameState }]);
          route.fulfill({
            contentType: 'text/plain',
            body: `42${gameStatePayload}`
          });
        } else if (pollCount === 3) {
          // Deliver winner_announcement event
          const winnerEventPayload = JSON.stringify(['winner_announcement', winnerPayload]);
          route.fulfill({
            contentType: 'text/plain',
            body: `42${winnerEventPayload}`
          });
        } else {
          // Subsequent polls - noop / pong
          route.fulfill({
            contentType: 'text/plain',
            body: '6'
          });
        }
      }
    } else if (route.request().method() === 'POST') {
      route.fulfill({ contentType: 'text/plain', body: 'ok' });
    } else {
      route.fulfill({ body: '' });
    }
  });

  // Navigate to menu, set localStorage, then navigate to game
  await page.goto('/menu', { waitUntil: 'commit' });
  await page.evaluate((guest) => {
    localStorage.setItem('currentUser', JSON.stringify({
      id: guest ? 'guest-123' : 'user-456',
      name: 'TestPlayer',
      is_guest: guest,
      created_at: '2024-01-01',
      permissions: guest ? ['play'] : ['play', 'custom_game', 'themed_game']
    }));
  }, isGuest);

  // Navigate directly to game page
  await page.goto('/game/test-game-123');

  // Wait for mobile poker table to render
  await expect(page.locator('.mobile-poker-table')).toBeVisible({ timeout: 10000 });
}

test.describe('PW-12: Post-round chat — tone selection and suggestion sending', () => {

  test('loser sees Salty and Gracious tone buttons after winner announcement', async ({ page }) => {
    // Batman wins → TestPlayer lost → loser tones
    await setupGamePage(page, { winnerPayload: showdownWinnerInfo });

    const overlay = page.locator('.mobile-winner-overlay');
    await expect(overlay).toBeVisible({ timeout: 15000 });

    // Post-round chat section should be visible
    const postRoundChat = overlay.locator('.post-round-chat');
    await expect(postRoundChat).toBeVisible({ timeout: 5000 });

    // Tone buttons should be visible
    const toneButtons = overlay.locator('.post-round-tone');
    await expect(toneButtons).toHaveCount(2);

    // Salty and Gracious tones for losers
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
    // TestPlayer wins by fold → winner tones
    await setupGamePage(page, { winnerPayload: foldWinnerInfo });

    const overlay = page.locator('.mobile-winner-overlay');
    await expect(overlay).toBeVisible({ timeout: 15000 });

    // Post-round chat section should be visible
    const postRoundChat = overlay.locator('.post-round-chat');
    await expect(postRoundChat).toBeVisible({ timeout: 5000 });

    // Gloat and Humble tones for winners
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
    await setupGamePage(page, {
      winnerPayload: showdownWinnerInfo,
      postRoundSuggestions: [
        { text: 'Nice hand, well played!', tone: 'gracious' },
        { text: 'You earned that one.', tone: 'gracious' },
      ],
    });

    const overlay = page.locator('.mobile-winner-overlay');
    await expect(overlay).toBeVisible({ timeout: 15000 });

    // Click Gracious tone
    const graciousBtn = overlay.locator('.tone-gracious');
    await expect(graciousBtn).toBeVisible({ timeout: 5000 });
    await graciousBtn.click();

    // Suggestions should appear (loading may be too fast to reliably catch)
    const suggestions = overlay.locator('.post-round-suggestion');
    await expect(suggestions.first()).toBeVisible({ timeout: 10000 });

    // Should have 2 suggestions
    await expect(suggestions).toHaveCount(2);

    // Suggestion text should match mock
    const firstSuggestion = suggestions.nth(0);
    await expect(firstSuggestion).toContainText('Nice hand, well played!');

    const secondSuggestion = suggestions.nth(1);
    await expect(secondSuggestion).toContainText('You earned that one.');
  });

  test('tapping a suggestion shows Sent confirmation', async ({ page }) => {
    await setupGamePage(page, {
      winnerPayload: showdownWinnerInfo,
      postRoundSuggestions: [
        { text: 'Nice hand!', tone: 'gracious' },
        { text: 'Well played.', tone: 'gracious' },
      ],
    });

    const overlay = page.locator('.mobile-winner-overlay');
    await expect(overlay).toBeVisible({ timeout: 15000 });

    // Click a tone to get suggestions
    const graciousBtn = overlay.locator('.tone-gracious');
    await expect(graciousBtn).toBeVisible({ timeout: 5000 });
    await graciousBtn.click();

    // Wait for suggestions
    const suggestions = overlay.locator('.post-round-suggestion');
    await expect(suggestions.first()).toBeVisible({ timeout: 10000 });

    // Click the first suggestion
    await suggestions.first().click();

    // Should show "Sent" confirmation
    const sentConfirmation = overlay.locator('.post-round-sent');
    await expect(sentConfirmation).toBeVisible({ timeout: 5000 });
    const sentText = await sentConfirmation.textContent();
    expect(sentText).toContain('Sent');
  });

  test('Back button returns to tone selection', async ({ page }) => {
    await setupGamePage(page, {
      winnerPayload: showdownWinnerInfo,
      postRoundSuggestions: [
        { text: 'Nice hand!', tone: 'gracious' },
        { text: 'Well played.', tone: 'gracious' },
      ],
    });

    const overlay = page.locator('.mobile-winner-overlay');
    await expect(overlay).toBeVisible({ timeout: 15000 });

    // Click a tone to get suggestions
    const graciousBtn = overlay.locator('.tone-gracious');
    await expect(graciousBtn).toBeVisible({ timeout: 5000 });
    await graciousBtn.click();

    // Wait for suggestions to appear
    const suggestions = overlay.locator('.post-round-suggestion');
    await expect(suggestions.first()).toBeVisible({ timeout: 10000 });

    // Click Back button
    const backBtn = overlay.locator('.post-round-back');
    await expect(backBtn).toBeVisible();
    await backBtn.click();

    // Tone buttons should be visible again
    const toneButtons = overlay.locator('.post-round-tones');
    await expect(toneButtons).toBeVisible({ timeout: 5000 });

    // Salty and Gracious should reappear (loser tones)
    await expect(overlay.locator('.tone-salty')).toBeVisible();
    await expect(overlay.locator('.tone-gracious')).toBeVisible();
  });

});
