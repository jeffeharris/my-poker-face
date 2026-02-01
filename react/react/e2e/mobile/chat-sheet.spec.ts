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
 * Build a game state with pre-existing chat messages.
 */
function buildGameStateWithMessages() {
  return buildGameState({
    messages: [
      { id: 'msg-1', sender: 'Batman', message: 'I am the night.', type: 'ai', timestamp: '2024-01-01T00:00:00Z' },
      { id: 'msg-2', sender: 'TestPlayer', message: 'Nice bluff!', type: 'player', timestamp: '2024-01-01T00:00:01Z' },
      { id: 'msg-3', sender: 'Gandalf', message: 'You shall not pass this river card!', type: 'ai', timestamp: '2024-01-01T00:00:02Z' },
    ],
  });
}

/**
 * Set up all mocks and navigate to the game page.
 */
async function setupGamePage(
  page: import('@playwright/test').Page,
  gameState: Record<string, unknown>,
  opts: { isGuest?: boolean } = {}
) {
  const isGuest = opts.isGuest !== false;

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
    route.fulfill({ json: gameState })
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

  // Mock Socket.IO polling transport
  let socketConnectSent = false;
  await page.route('**/socket.io/**', route => {
    const url = route.request().url();
    if (url.includes('transport=polling') && route.request().method() === 'GET') {
      if (!url.includes('sid=')) {
        route.fulfill({
          contentType: 'text/plain',
          body: '0{"sid":"fake-sid","upgrades":[],"pingInterval":25000,"pingTimeout":20000}'
        });
      } else if (!socketConnectSent) {
        socketConnectSent = true;
        route.fulfill({
          contentType: 'text/plain',
          body: '40{"sid":"fake-socket-sid"}'
        });
      } else {
        route.fulfill({
          contentType: 'text/plain',
          body: '6'
        });
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

test.describe('PW-09: Mobile chat sheet — open, tab switch, send message, dismiss', () => {

  test('tapping chat button opens the MobileChatSheet', async ({ page }) => {
    const gameState = buildGameState();
    await setupGamePage(page, gameState);

    // Chat button should be visible
    const chatBtn = page.locator('.action-btn.chat-btn');
    await expect(chatBtn).toBeVisible({ timeout: 5000 });

    // Tap chat button
    await chatBtn.click();

    // Chat sheet overlay and sheet should appear
    const overlay = page.locator('.mcs-overlay');
    await expect(overlay).toBeVisible({ timeout: 5000 });

    const sheet = page.locator('.mcs-sheet');
    await expect(sheet).toBeVisible({ timeout: 5000 });
  });

  test('chat sheet has Quick Chat and Keyboard tabs', async ({ page }) => {
    const gameState = buildGameState();
    await setupGamePage(page, gameState);

    await page.locator('.action-btn.chat-btn').click();

    const sheet = page.locator('.mcs-sheet');
    await expect(sheet).toBeVisible({ timeout: 5000 });

    // Two tabs should be visible
    const tabs = sheet.locator('.mcs-tab');
    const tabCount = await tabs.count();
    expect(tabCount).toBe(2);

    // Check tab contents — one should mention Quick Chat, other Keyboard
    const tabTexts = await tabs.allTextContents();
    const allText = tabTexts.join(' ').toLowerCase();
    expect(allText).toContain('quick');
    expect(allText).toMatch(/keyboard|type/i);
  });

  test('guest user has Quick Chat tab disabled', async ({ page }) => {
    const gameState = buildGameState();
    await setupGamePage(page, gameState, { isGuest: true });

    await page.locator('.action-btn.chat-btn').click();

    const sheet = page.locator('.mcs-sheet');
    await expect(sheet).toBeVisible({ timeout: 5000 });

    // Quick Chat tab should be disabled for guest
    const disabledTab = sheet.locator('.mcs-tab-disabled');
    await expect(disabledTab).toBeVisible();

    // Disabled tab should show "Sign in" text
    const disabledText = await disabledTab.textContent();
    expect(disabledText?.toLowerCase()).toContain('sign in');
  });

  test('keyboard tab shows text input and send button', async ({ page }) => {
    const gameState = buildGameState();
    await setupGamePage(page, gameState, { isGuest: true });

    await page.locator('.action-btn.chat-btn').click();

    const sheet = page.locator('.mcs-sheet');
    await expect(sheet).toBeVisible({ timeout: 5000 });

    // For guest, keyboard tab is default — input should be visible
    const textInput = sheet.locator('.mcs-text-input');
    await expect(textInput).toBeVisible();

    const sendBtn = sheet.locator('.mcs-send-btn');
    await expect(sendBtn).toBeVisible();
  });

  test('typing a message activates the send button', async ({ page }) => {
    const gameState = buildGameState();
    await setupGamePage(page, gameState, { isGuest: true });

    await page.locator('.action-btn.chat-btn').click();

    const sheet = page.locator('.mcs-sheet');
    await expect(sheet).toBeVisible({ timeout: 5000 });

    // Type a message
    const textInput = sheet.locator('.mcs-text-input');
    await textInput.fill('Hello from mobile!');

    // Send button should become active
    const sendBtn = sheet.locator('.mcs-send-btn.mcs-send-active');
    await expect(sendBtn).toBeVisible({ timeout: 3000 });
  });

  test('tapping send submits the message', async ({ page }) => {
    const gameState = buildGameState();
    await setupGamePage(page, gameState, { isGuest: true });

    // Track chat requests
    const chatRequests: string[] = [];
    await page.route('**/api/game/*/chat', route => {
      chatRequests.push(route.request().postData() || '');
      route.fulfill({ json: { success: true } });
    });

    await page.locator('.action-btn.chat-btn').click();

    const sheet = page.locator('.mcs-sheet');
    await expect(sheet).toBeVisible({ timeout: 5000 });

    // Type and send
    const textInput = sheet.locator('.mcs-text-input');
    await textInput.fill('Hello from mobile!');

    const sendBtn = sheet.locator('.mcs-send-btn');
    await sendBtn.click();

    // Input should be cleared after send
    await expect(textInput).toHaveValue('', { timeout: 3000 });
  });

  test('close button dismisses the chat sheet', async ({ page }) => {
    const gameState = buildGameState();
    await setupGamePage(page, gameState);

    await page.locator('.action-btn.chat-btn').click();

    const sheet = page.locator('.mcs-sheet');
    await expect(sheet).toBeVisible({ timeout: 5000 });

    // Tap close button
    const closeBtn = sheet.locator('.mcs-close-btn');
    await expect(closeBtn).toBeVisible();
    await closeBtn.click();

    // Sheet and overlay should disappear
    await expect(sheet).not.toBeVisible({ timeout: 5000 });
  });

  test('shows "No messages yet" when message list is empty', async ({ page }) => {
    const gameState = buildGameState({ messages: [] });
    await setupGamePage(page, gameState);

    await page.locator('.action-btn.chat-btn').click();

    const sheet = page.locator('.mcs-sheet');
    await expect(sheet).toBeVisible({ timeout: 5000 });

    // Empty state should show
    const emptyState = sheet.locator('.mcs-empty');
    await expect(emptyState).toBeVisible();
    const text = await emptyState.textContent();
    expect(text?.toLowerCase()).toContain('no messages');
  });

  test('messages area shows existing messages', async ({ page }) => {
    const gameState = buildGameStateWithMessages();
    await setupGamePage(page, gameState);

    const chatBtn = page.locator('.action-btn.chat-btn');
    await expect(chatBtn).toBeVisible({ timeout: 5000 });
    await chatBtn.click();

    const sheet = page.locator('.mcs-sheet');
    await expect(sheet).toBeVisible({ timeout: 5000 });

    // Messages should be rendered
    const messages = sheet.locator('.mcs-msg');
    const count = await messages.count();
    expect(count).toBeGreaterThan(0);
  });

});
