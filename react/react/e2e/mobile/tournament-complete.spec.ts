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

/** Tournament result fixture: TestPlayer wins (1st place) */
const tournamentResultWinner = {
  winner: 'TestPlayer',
  standings: [
    { player_name: 'TestPlayer', is_human: true, finishing_position: 1, eliminated_by: null, eliminated_at_hand: null },
    { player_name: 'Batman', is_human: false, finishing_position: 2, eliminated_by: 'TestPlayer', eliminated_at_hand: 28 },
    { player_name: 'Gandalf', is_human: false, finishing_position: 3, eliminated_by: 'Batman', eliminated_at_hand: 15 },
  ],
  total_hands: 28,
  biggest_pot: 4500,
  human_position: 1,
  game_id: 'test-game-123',
};

/** Tournament result fixture: TestPlayer eliminated (2nd place) */
const tournamentResultEliminated = {
  winner: 'Batman',
  standings: [
    { player_name: 'Batman', is_human: false, finishing_position: 1, eliminated_by: null, eliminated_at_hand: null },
    { player_name: 'TestPlayer', is_human: true, finishing_position: 2, eliminated_by: 'Batman', eliminated_at_hand: 28 },
    { player_name: 'Gandalf', is_human: false, finishing_position: 3, eliminated_by: 'Batman', eliminated_at_hand: 15 },
  ],
  total_hands: 28,
  biggest_pot: 4500,
  human_position: 2,
  game_id: 'test-game-123',
};

/**
 * Set up all mocks and navigate to the game page.
 * Delivers a tournament_complete event via socket.io after initial load.
 */
async function setupGamePage(
  page: import('@playwright/test').Page,
  opts: {
    isGuest?: boolean;
    tournamentPayload?: Record<string, unknown>;
  } = {}
) {
  const isGuest = opts.isGuest !== false;
  const initialGameState = buildGameState();
  const tournamentPayload = opts.tournamentPayload || tournamentResultWinner;

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

  // Mock end_game endpoint (called when returning to menu)
  await page.route('**/api/end_game/**', route =>
    route.fulfill({ json: { success: true } })
  );

  // Socket.IO mock: deliver tournament_complete event after connect
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
            body: '40{"sid":"fake-socket-id"}'
          });
        } else if (pollCount === 2) {
          // Deliver initial game state via socket
          const gameStatePayload = JSON.stringify(['update_game_state', { game_state: initialGameState }]);
          route.fulfill({
            contentType: 'text/plain',
            body: `42${gameStatePayload}`
          });
        } else if (pollCount === 3) {
          // Deliver tournament_complete event
          const tournamentPayloadStr = JSON.stringify(['tournament_complete', tournamentPayload]);
          route.fulfill({
            contentType: 'text/plain',
            body: `42${tournamentPayloadStr}`
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

test.describe('PW-13: Tournament complete screen displays final standings', () => {

  test('tournament complete screen renders when player wins', async ({ page }) => {
    await setupGamePage(page, { tournamentPayload: tournamentResultWinner });

    // Wait for the tournament complete overlay to appear
    const overlay = page.locator('.tournament-complete');
    await expect(overlay).toBeVisible({ timeout: 15000 });

    // Title should show "CHAMPION!" for the winner
    const title = overlay.locator('.tournament-title');
    await expect(title).toBeVisible();
    const titleText = await title.textContent();
    expect(titleText).toContain('CHAMPION!');

    // Winner announcement text
    const winnerAnnouncement = overlay.locator('.winner-announcement');
    await expect(winnerAnnouncement).toBeVisible();
    const announcementText = await winnerAnnouncement.textContent();
    expect(announcementText).toContain('TestPlayer');
    expect(announcementText).toContain('wins the tournament');
  });

  test('player finishing position is displayed', async ({ page }) => {
    await setupGamePage(page, { tournamentPayload: tournamentResultWinner });

    const overlay = page.locator('.tournament-complete');
    await expect(overlay).toBeVisible({ timeout: 15000 });

    // Your Result section shows finishing position
    const yourResult = overlay.locator('.your-result');
    await expect(yourResult).toBeVisible();

    // Should show "1st" for the winner
    const resultPosition = overlay.locator('.result-position');
    await expect(resultPosition).toBeVisible();
    const positionText = await resultPosition.textContent();
    expect(positionText).toContain('1st');

    // Winner should have the winner class
    await expect(yourResult).toHaveClass(/winner/);
  });

  test('final standings table shows all players in order', async ({ page }) => {
    await setupGamePage(page, { tournamentPayload: tournamentResultWinner });

    const overlay = page.locator('.tournament-complete');
    await expect(overlay).toBeVisible({ timeout: 15000 });

    // Standings section visible
    const standingsSection = overlay.locator('.standings-section');
    await expect(standingsSection).toBeVisible();

    // Title should say "Final Standings"
    const standingsTitle = overlay.locator('.standings-title');
    await expect(standingsTitle).toContainText('Final Standings');

    // 3 standing rows (one per player)
    const standingRows = overlay.locator('.standing-row');
    await expect(standingRows).toHaveCount(3);

    // First row should be 1st place (TestPlayer - winner)
    const firstRow = standingRows.nth(0);
    await expect(firstRow.locator('.position')).toContainText('1st');
    await expect(firstRow.locator('.name')).toContainText('TestPlayer');
    await expect(firstRow).toHaveClass(/winner/);
    await expect(firstRow).toHaveClass(/human/);

    // Second row should be 2nd place (Batman)
    const secondRow = standingRows.nth(1);
    await expect(secondRow.locator('.position')).toContainText('2nd');
    await expect(secondRow.locator('.name')).toContainText('Batman');

    // Third row should be 3rd place (Gandalf)
    const thirdRow = standingRows.nth(2);
    await expect(thirdRow.locator('.position')).toContainText('3rd');
    await expect(thirdRow.locator('.name')).toContainText('Gandalf');
  });

  test('tournament stats show hands played and biggest pot', async ({ page }) => {
    await setupGamePage(page, { tournamentPayload: tournamentResultWinner });

    const overlay = page.locator('.tournament-complete');
    await expect(overlay).toBeVisible({ timeout: 15000 });

    // Stats section visible
    const stats = overlay.locator('.tournament-stats');
    await expect(stats).toBeVisible();

    // Should show total hands
    const statValues = overlay.locator('.stat-value');
    const statLabels = overlay.locator('.stat-label');

    // First stat: hands played
    await expect(statValues.nth(0)).toContainText('28');
    await expect(statLabels.nth(0)).toContainText('Hands Played');

    // Second stat: biggest pot
    const biggestPotText = await statValues.nth(1).textContent();
    expect(biggestPotText).toContain('4,500');
    await expect(statLabels.nth(1)).toContainText('Biggest Pot');
  });

  test('return to menu button is visible', async ({ page }) => {
    await setupGamePage(page, { tournamentPayload: tournamentResultWinner });

    const overlay = page.locator('.tournament-complete');
    await expect(overlay).toBeVisible({ timeout: 15000 });

    // Continue / Return to Menu button
    const continueBtn = overlay.locator('.continue-button');
    await expect(continueBtn).toBeVisible();
    const btnText = await continueBtn.textContent();
    expect(btnText).toContain('Return to Menu');
  });

  test('eliminated player sees correct title and position', async ({ page }) => {
    await setupGamePage(page, { tournamentPayload: tournamentResultEliminated });

    const overlay = page.locator('.tournament-complete');
    await expect(overlay).toBeVisible({ timeout: 15000 });

    // Title should show "Tournament Complete" (not CHAMPION)
    const title = overlay.locator('.tournament-title');
    const titleText = await title.textContent();
    expect(titleText).not.toContain('CHAMPION');

    // Winner announcement should mention Batman
    const winnerAnnouncement = overlay.locator('.winner-announcement');
    await expect(winnerAnnouncement).toBeVisible();
    const announcementText = await winnerAnnouncement.textContent();
    expect(announcementText).toContain('Batman');

    // Your Result section shows 2nd place
    const resultPosition = overlay.locator('.result-position');
    await expect(resultPosition).toBeVisible();
    const positionText = await resultPosition.textContent();
    expect(positionText).toContain('2nd');

    // Should show "Eliminated by Batman"
    const eliminatedBy = overlay.locator('.eliminated-by');
    await expect(eliminatedBy).toBeVisible();
    const eliminatedText = await eliminatedBy.textContent();
    expect(eliminatedText).toContain('Batman');
  });

  test('elimination details shown in standings', async ({ page }) => {
    await setupGamePage(page, { tournamentPayload: tournamentResultEliminated });

    const overlay = page.locator('.tournament-complete');
    await expect(overlay).toBeVisible({ timeout: 15000 });

    const standingRows = overlay.locator('.standing-row');
    await expect(standingRows).toHaveCount(3);

    // Winner row shows "Winner" text
    const winnerRow = standingRows.nth(0);
    await expect(winnerRow.locator('.eliminated-info')).toContainText('Winner');

    // 2nd place row shows eliminated by info
    const secondRow = standingRows.nth(1);
    await expect(secondRow.locator('.eliminated-info')).toContainText('by Batman');

    // 3rd place row shows eliminated by info
    const thirdRow = standingRows.nth(2);
    await expect(thirdRow.locator('.eliminated-info')).toContainText('by Batman');
  });

});
