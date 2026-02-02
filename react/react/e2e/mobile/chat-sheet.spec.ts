import { test, expect } from '@playwright/test';
import { mockGamePageRoutes, navigateToGamePage, buildGameState } from '../helpers';

function buildGameStateWithMessages() {
  return buildGameState(['fold', 'call', 'raise'], {
    messages: [
      { id: 'msg-1', sender: 'Batman', message: 'I am the night.', type: 'ai', timestamp: '2024-01-01T00:00:00Z' },
      { id: 'msg-2', sender: 'TestPlayer', message: 'Nice bluff!', type: 'player', timestamp: '2024-01-01T00:00:01Z' },
      { id: 'msg-3', sender: 'Gandalf', message: 'You shall not pass this river card!', type: 'ai', timestamp: '2024-01-01T00:00:02Z' },
    ],
  });
}

test.describe('PW-09: Mobile chat sheet — open, tab switch, send message, dismiss', () => {

  test('tapping chat button opens the MobileChatSheet', async ({ page }) => {
    const ctx = await mockGamePageRoutes(page, { gameState: buildGameState() });
    await navigateToGamePage(page, { mockContext: ctx });

    const chatBtn = page.getByTestId('action-btn-chat');
    await expect(chatBtn).toBeVisible({ timeout: 5000 });
    await chatBtn.click();

    const overlay = page.locator('.mcs-overlay');
    await expect(overlay).toBeVisible({ timeout: 5000 });

    const sheet = page.locator('.mcs-sheet');
    await expect(sheet).toBeVisible({ timeout: 5000 });
  });

  test('chat sheet has Quick Chat and Keyboard tabs', async ({ page }) => {
    const ctx = await mockGamePageRoutes(page, { gameState: buildGameState() });
    await navigateToGamePage(page, { mockContext: ctx });

    await page.getByTestId('action-btn-chat').click();

    const sheet = page.locator('.mcs-sheet');
    await expect(sheet).toBeVisible({ timeout: 5000 });

    const tabs = sheet.locator('.mcs-tab');
    const tabCount = await tabs.count();
    expect(tabCount).toBe(2);

    const tabTexts = await tabs.allTextContents();
    const allText = tabTexts.join(' ').toLowerCase();
    expect(allText).toContain('quick');
    expect(allText).toMatch(/keyboard|type/i);
  });

  test('guest user has Quick Chat tab disabled', async ({ page }) => {
    const ctx = await mockGamePageRoutes(page, { gameState: buildGameState(), isGuest: true });
    await navigateToGamePage(page, { mockContext: ctx });

    await page.getByTestId('action-btn-chat').click();

    const sheet = page.locator('.mcs-sheet');
    await expect(sheet).toBeVisible({ timeout: 5000 });

    const disabledTab = sheet.locator('.mcs-tab-disabled');
    await expect(disabledTab).toBeVisible();

    const disabledText = await disabledTab.textContent();
    expect(disabledText?.toLowerCase()).toContain('sign in');
  });

  test('keyboard tab shows text input and send button', async ({ page }) => {
    const ctx = await mockGamePageRoutes(page, { gameState: buildGameState(), isGuest: true });
    await navigateToGamePage(page, { mockContext: ctx });

    await page.getByTestId('action-btn-chat').click();

    const sheet = page.locator('.mcs-sheet');
    await expect(sheet).toBeVisible({ timeout: 5000 });

    const textInput = sheet.locator('.mcs-text-input');
    await expect(textInput).toBeVisible();

    const sendBtn = sheet.locator('.mcs-send-btn');
    await expect(sendBtn).toBeVisible();
  });

  test('typing a message activates the send button', async ({ page }) => {
    const ctx = await mockGamePageRoutes(page, { gameState: buildGameState(), isGuest: true });
    await navigateToGamePage(page, { mockContext: ctx });

    await page.getByTestId('action-btn-chat').click();

    const sheet = page.locator('.mcs-sheet');
    await expect(sheet).toBeVisible({ timeout: 5000 });

    const textInput = sheet.locator('.mcs-text-input');
    await textInput.fill('Hello from mobile!');

    const sendBtn = sheet.locator('.mcs-send-btn.mcs-send-active');
    await expect(sendBtn).toBeVisible({ timeout: 3000 });
  });

  test('tapping send submits the message', async ({ page }) => {
    const ctx = await mockGamePageRoutes(page, { gameState: buildGameState(), isGuest: true });
    await navigateToGamePage(page, { mockContext: ctx });

    const chatRequests: string[] = [];
    await page.route('**/api/game/*/chat', route => {
      chatRequests.push(route.request().postData() || '');
      route.fulfill({ json: { success: true } });
    });

    await page.getByTestId('action-btn-chat').click();

    const sheet = page.locator('.mcs-sheet');
    await expect(sheet).toBeVisible({ timeout: 5000 });

    const textInput = sheet.locator('.mcs-text-input');
    await textInput.fill('Hello from mobile!');

    const sendBtn = sheet.locator('.mcs-send-btn');
    await sendBtn.click();

    await expect(textInput).toHaveValue('', { timeout: 3000 });
  });

  test('close button dismisses the chat sheet', async ({ page }) => {
    const ctx = await mockGamePageRoutes(page, { gameState: buildGameState() });
    await navigateToGamePage(page, { mockContext: ctx });

    await page.getByTestId('action-btn-chat').click();

    const sheet = page.locator('.mcs-sheet');
    await expect(sheet).toBeVisible({ timeout: 5000 });

    const closeBtn = sheet.locator('.mcs-close-btn');
    await expect(closeBtn).toBeVisible();
    await closeBtn.click();

    await expect(sheet).not.toBeVisible({ timeout: 5000 });
  });

  test('shows "No messages yet" when message list is empty', async ({ page }) => {
    const ctx = await mockGamePageRoutes(page, { gameState: buildGameState(['fold', 'call', 'raise'], { messages: [] }) });
    await navigateToGamePage(page, { mockContext: ctx });

    await page.getByTestId('action-btn-chat').click();

    const sheet = page.locator('.mcs-sheet');
    await expect(sheet).toBeVisible({ timeout: 5000 });

    const emptyState = sheet.locator('.mcs-empty');
    await expect(emptyState).toBeVisible();
    const text = await emptyState.textContent();
    expect(text?.toLowerCase()).toContain('no messages');
  });

  test('messages area shows existing messages', async ({ page }) => {
    const ctx = await mockGamePageRoutes(page, { gameState: buildGameStateWithMessages() });
    await navigateToGamePage(page, { mockContext: ctx });

    const chatBtn = page.getByTestId('action-btn-chat');
    await expect(chatBtn).toBeVisible({ timeout: 5000 });
    await chatBtn.click();

    const sheet = page.locator('.mcs-sheet');
    await expect(sheet).toBeVisible({ timeout: 5000 });

    const messages = sheet.locator('.mcs-msg');
    const count = await messages.count();
    expect(count).toBeGreaterThan(0);
  });

});
