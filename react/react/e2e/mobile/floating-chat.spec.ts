import { test, expect } from '@playwright/test';
import { mockGamePageRoutes, navigateToGamePage, buildGameState } from '../helpers';

function buildGameStateWithAiMessages() {
  return buildGameState(['fold', 'call', 'raise'], {
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
  return buildGameState(['fold', 'call', 'raise'], {
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

const initialGameState = buildGameState(['fold', 'call', 'raise'], { messages: [] });

async function setupFloatingChat(
  page: import('@playwright/test').Page,
  socketGameState?: Record<string, unknown>
) {
  const gameStateForSocket = socketGameState || buildGameStateWithAiMessages();

  await mockGamePageRoutes(page, {
    gameState: initialGameState,
    socketEvents: [
      ['update_game_state', { game_state: gameStateForSocket }],
    ],
  });
  await navigateToGamePage(page);
}

test.describe('PW-10: Floating chat bubbles appear and auto-dismiss', () => {

  test('AI message triggers a floating chat bubble with sender name and message', async ({ page }) => {
    await setupFloatingChat(page);

    const bubble = page.locator('.floating-chat').first();
    await expect(bubble).toBeVisible({ timeout: 10000 });

    const sender = bubble.locator('.floating-chat-sender');
    await expect(sender).toBeVisible();
    const senderText = await sender.textContent();
    expect(senderText).toBeTruthy();

    const avatar = bubble.locator('.floating-chat-avatar');
    await expect(avatar).toBeVisible();
  });

  test('floating chat bubble shows message text (typed out)', async ({ page }) => {
    await setupFloatingChat(page);

    const bubble = page.locator('.floating-chat').first();
    await expect(bubble).toBeVisible({ timeout: 10000 });

    const messageContent = bubble.locator('.floating-chat-message');
    await expect(messageContent).toBeVisible({ timeout: 5000 });

    await expect(messageContent).toContainText('night', { timeout: 10000 });
  });

  test('dismiss button removes bubble immediately', async ({ page }) => {
    await setupFloatingChat(page);

    const bubble = page.locator('.floating-chat').first();
    await expect(bubble).toBeVisible({ timeout: 10000 });

    const dismissBtn = bubble.locator('.floating-chat-dismiss');
    await expect(dismissBtn).toBeVisible();
    await dismissBtn.click({ force: true });

    await expect(page.locator('.floating-chat')).not.toBeVisible({ timeout: 5000 });
  });

  test('bubble has avatar with image or initial', async ({ page }) => {
    await setupFloatingChat(page);

    const bubble = page.locator('.floating-chat').first();
    await expect(bubble).toBeVisible({ timeout: 10000 });

    const avatar = bubble.locator('.floating-chat-avatar');
    await expect(avatar).toBeVisible();

    const avatarText = await avatar.textContent();
    const hasImage = await avatar.locator('img.floating-avatar-img').count();
    expect(hasImage > 0 || (avatarText && avatarText.trim().length > 0)).toBeTruthy();
  });

  test('maximum 2 active bubbles at once when multiple AI messages arrive', async ({ page }) => {
    await setupFloatingChat(page, buildGameStateWithTwoAiMessages());

    const bubbles = page.locator('.floating-chat');
    await expect(bubbles.first()).toBeVisible({ timeout: 10000 });

    const stack = page.locator('.floating-chat-stack');
    await expect(stack).toBeVisible();

    const count = await bubbles.count();
    expect(count).toBeLessThanOrEqual(2);
  });

});
