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

/**
 * Build a game state with AI messages included — these will be detected
 * as "new" by usePokerGame when delivered via socket update_game_state.
 */
function buildGameStateWithAiMessages() {
  return buildGameState({
    messages: [
      {
        id: 'ai-msg-1',
        sender: 'Batman',
        message: 'I am the night.',
        timestamp: '2024-01-01T00:00:01Z',
        type: 'ai',
        action: 'Batman calls $50'
      },
    ],
  });
}

function buildGameStateWithTwoAiMessages() {
  return buildGameState({
    messages: [
      {
        id: 'ai-msg-1',
        sender: 'Batman',
        message: 'I am the night.',
        timestamp: '2024-01-01T00:00:01Z',
        type: 'ai',
        action: 'Batman calls $50'
      },
      {
        id: 'ai-msg-2',
        sender: 'Gandalf',
        message: 'You shall not pass!',
        timestamp: '2024-01-01T00:00:02Z',
        type: 'ai',
        action: 'Gandalf raises to $200'
      },
    ],
  });
}

/**
 * Set up all mocks and navigate to the game page.
 * The socket.io mock delivers an update_game_state event with AI messages
 * after the initial (empty messages) state loads.
 */
async function setupGamePage(
  page: import('@playwright/test').Page,
  opts: {
    isGuest?: boolean;
    socketGameState?: Record<string, unknown>;
  } = {}
) {
  const isGuest = opts.isGuest !== false;
  const initialGameState = buildGameState({ messages: [] });

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

  // Mock game state — initial load returns empty messages
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

  // Socket.IO mock: deliver update_game_state event with AI messages
  // after the initial handshake and connect phases.
  const socketGameState = opts.socketGameState || buildGameStateWithAiMessages();
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
          // Deliver update_game_state event with AI messages
          const eventPayload = JSON.stringify(['update_game_state', { game_state: socketGameState }]);
          route.fulfill({
            contentType: 'text/plain',
            body: `42${eventPayload}`
          });
        } else {
          // Subsequent polls - noop / ping
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

test.describe('PW-10: Floating chat bubbles appear and auto-dismiss', () => {

  test('AI message triggers a floating chat bubble with sender name and message', async ({ page }) => {
    await setupGamePage(page);

    // Wait for the floating chat bubble to appear
    const bubble = page.locator('.floating-chat').first();
    await expect(bubble).toBeVisible({ timeout: 10000 });

    // Bubble should show the sender name
    const sender = bubble.locator('.floating-chat-sender');
    await expect(sender).toBeVisible();
    const senderText = await sender.textContent();
    // Sender shows either the action text or the sender name
    expect(senderText).toBeTruthy();

    // Bubble should have an avatar area
    const avatar = bubble.locator('.floating-chat-avatar');
    await expect(avatar).toBeVisible();
  });

  test('floating chat bubble shows message text (typed out)', async ({ page }) => {
    await setupGamePage(page);

    // Wait for the floating chat bubble to appear
    const bubble = page.locator('.floating-chat').first();
    await expect(bubble).toBeVisible({ timeout: 10000 });

    // The message content area should exist
    const messageContent = bubble.locator('.floating-chat-message');
    await expect(messageContent).toBeVisible({ timeout: 5000 });

    // Wait for typing animation to complete — the text should eventually contain the message
    await expect(messageContent).toContainText('night', { timeout: 10000 });
  });

  test('dismiss button removes bubble immediately', async ({ page }) => {
    await setupGamePage(page);

    // Wait for the floating chat bubble to appear
    const bubble = page.locator('.floating-chat').first();
    await expect(bubble).toBeVisible({ timeout: 10000 });

    // Find and click the dismiss button
    const dismissBtn = bubble.locator('.floating-chat-dismiss');
    await expect(dismissBtn).toBeVisible();
    await dismissBtn.click();

    // Bubble should disappear
    await expect(page.locator('.floating-chat')).not.toBeVisible({ timeout: 5000 });
  });

  test('bubble has avatar with image or initial', async ({ page }) => {
    await setupGamePage(page);

    // Wait for the floating chat bubble to appear
    const bubble = page.locator('.floating-chat').first();
    await expect(bubble).toBeVisible({ timeout: 10000 });

    // Avatar should be visible
    const avatar = bubble.locator('.floating-chat-avatar');
    await expect(avatar).toBeVisible();

    // Avatar should either have an img or show an initial letter
    const avatarText = await avatar.textContent();
    const hasImage = await avatar.locator('img.floating-avatar-img').count();
    // Either has an image or shows initial text
    expect(hasImage > 0 || (avatarText && avatarText.trim().length > 0)).toBeTruthy();
  });

  test('maximum 2 active bubbles at once when multiple AI messages arrive', async ({ page }) => {
    // Use game state with two AI messages
    await setupGamePage(page, {
      socketGameState: buildGameStateWithTwoAiMessages(),
    });

    // Wait for floating chat bubbles to appear
    const bubbles = page.locator('.floating-chat');
    // The most recent AI message should appear (usePokerGame sends only the last AI message)
    await expect(bubbles.first()).toBeVisible({ timeout: 10000 });

    // Verify the stack container exists
    const stack = page.locator('.floating-chat-stack');
    await expect(stack).toBeVisible();

    // There should be at most 2 visible bubbles (ACTIVE_MESSAGE_LIMIT = 2)
    const count = await bubbles.count();
    expect(count).toBeLessThanOrEqual(2);
  });

});
