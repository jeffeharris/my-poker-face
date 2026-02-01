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

/**
 * Set up all mocks and navigate to the game page.
 * After the initial game state loads, delivers a winner_announcement event via socket.io.
 */
async function setupGamePage(
  page: import('@playwright/test').Page,
  opts: {
    isGuest?: boolean;
    winnerPayload?: Record<string, unknown>;
  } = {}
) {
  const isGuest = opts.isGuest !== false;
  const initialGameState = buildGameState();
  const winnerPayload = opts.winnerPayload || showdownWinnerInfo;

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

  // Mock post-round chat
  await page.route('**/api/game/*/post-round-chat*', route =>
    route.fulfill({
      json: {
        suggestions: [
          { text: 'Nice hand!', tone: 'humble' },
          { text: 'Got lucky there.', tone: 'humble' }
        ]
      }
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

test.describe('PW-11: Winner announcement shows after hand and auto-dismisses', () => {

  test('showdown winner overlay appears with winner name and amount', async ({ page }) => {
    await setupGamePage(page, { winnerPayload: showdownWinnerInfo });

    // Wait for the winner overlay to appear
    const overlay = page.locator('.mobile-winner-overlay');
    await expect(overlay).toBeVisible({ timeout: 15000 });

    // Winner name should show "Batman wins"
    const winnerNames = overlay.locator('.winner-names');
    await expect(winnerNames).toBeVisible();
    const nameText = await winnerNames.textContent();
    expect(nameText).toContain('Batman');
    expect(nameText).toContain('wins');

    // Winner amount should show $300
    const winnerAmount = overlay.locator('.winner-amount');
    await expect(winnerAmount).toBeVisible();
    const amountText = await winnerAmount.textContent();
    expect(amountText).toContain('300');

    // Hand name should show "Pair of Kings"
    const handName = overlay.locator('.winner-hand-name');
    await expect(handName).toBeVisible();
    const handText = await handName.textContent();
    expect(handText).toContain('Pair of Kings');
  });

  test('showdown displays community cards and player hands', async ({ page }) => {
    await setupGamePage(page, { winnerPayload: showdownWinnerInfo });

    const overlay = page.locator('.mobile-winner-overlay');
    await expect(overlay).toBeVisible({ timeout: 15000 });

    // Wait for cards to be revealed (800ms delay in component)
    const showdownSection = overlay.locator('.showdown-section');
    await expect(showdownSection).toBeVisible({ timeout: 5000 });

    // Community cards should be displayed
    const communitySection = overlay.locator('.community-section');
    await expect(communitySection).toBeVisible();

    // Player showdown hands should be displayed
    const playerShowdowns = overlay.locator('.player-showdown');
    const count = await playerShowdowns.count();
    expect(count).toBe(2); // Batman and TestPlayer

    // Winner should have the winner class
    const winnerHand = overlay.locator('.player-showdown.winner');
    await expect(winnerHand).toBeVisible();
  });

  test('showdown has Continue button', async ({ page }) => {
    await setupGamePage(page, { winnerPayload: showdownWinnerInfo });

    const overlay = page.locator('.mobile-winner-overlay');
    await expect(overlay).toBeVisible({ timeout: 15000 });

    // Continue button should be visible
    const dismissBtn = overlay.locator('.dismiss-btn');
    await expect(dismissBtn).toBeVisible();
    const btnText = await dismissBtn.textContent();
    expect(btnText).toContain('Continue');
  });

  test('fold winner shows name, amount, and "All opponents folded"', async ({ page }) => {
    await setupGamePage(page, { winnerPayload: foldWinnerInfo });

    const overlay = page.locator('.mobile-winner-overlay');
    await expect(overlay).toBeVisible({ timeout: 15000 });

    // No showdown section should exist
    const showdownSection = overlay.locator('.showdown-section');
    await expect(showdownSection).not.toBeVisible();

    // Instead, the no-showdown-winner section should be visible
    const noShowdown = overlay.locator('.no-showdown-winner');
    await expect(noShowdown).toBeVisible();

    // Winner name
    const noShowdownName = overlay.locator('.no-showdown-name');
    await expect(noShowdownName).toBeVisible();
    const nameText = await noShowdownName.textContent();
    expect(nameText).toContain('TestPlayer');

    // Amount
    const noShowdownAmount = overlay.locator('.no-showdown-amount');
    await expect(noShowdownAmount).toBeVisible();
    const amountText = await noShowdownAmount.textContent();
    expect(amountText).toContain('150');

    // "All opponents folded" text
    const foldedText = overlay.locator('.no-showdown-text');
    await expect(foldedText).toBeVisible();
    const text = await foldedText.textContent();
    expect(text).toContain('All opponents folded');
  });

  test('fold winner has no showdown cards section', async ({ page }) => {
    await setupGamePage(page, { winnerPayload: foldWinnerInfo });

    const overlay = page.locator('.mobile-winner-overlay');
    await expect(overlay).toBeVisible({ timeout: 15000 });

    // Showdown section should NOT be visible
    const showdownSection = overlay.locator('.showdown-section');
    await expect(showdownSection).not.toBeVisible();

    // Continue button should still be present
    const dismissBtn = overlay.locator('.dismiss-btn');
    await expect(dismissBtn).toBeVisible();
  });

  test('clicking Continue dismisses the winner overlay', async ({ page }) => {
    await setupGamePage(page, { winnerPayload: foldWinnerInfo });

    const overlay = page.locator('.mobile-winner-overlay');
    await expect(overlay).toBeVisible({ timeout: 15000 });

    // Click Continue
    const dismissBtn = overlay.locator('.dismiss-btn');
    await dismissBtn.click();

    // Overlay should disappear
    await expect(overlay).not.toBeVisible({ timeout: 5000 });
  });

});
