import { test, expect } from '@playwright/test';
import { mockGamePageRoutes, navigateToGamePage, buildGameState } from '../helpers';

test.describe('PW-06: Mobile action buttons display correct options per game state', () => {

  test('pre-flop with fold/call/raise shows Fold, Call, Raise buttons', async ({ page }) => {
    const ctx = await mockGamePageRoutes(page, { gameState: buildGameState(['fold', 'call', 'raise']) });
    await navigateToGamePage(page, { mockContext: ctx });

    const actionButtons = page.locator('.mobile-action-buttons');
    await expect(actionButtons).toBeVisible();

    // Fold button visible
    await expect(page.locator('.action-btn.fold-btn')).toBeVisible();

    // Call button visible with amount text
    const callBtn = page.locator('.action-btn.call-btn');
    await expect(callBtn).toBeVisible();

    // Raise button visible
    await expect(page.locator('.action-btn.raise-btn')).toBeVisible();

    // Check button should NOT be visible
    await expect(page.locator('.action-btn.check-btn')).not.toBeVisible();
  });

  test('big blind option: fold/check/raise shows Fold, Check, Raise buttons', async ({ page }) => {
    const ctx = await mockGamePageRoutes(page, { gameState: buildGameState(['fold', 'check', 'raise']) });
    await navigateToGamePage(page, { mockContext: ctx });

    const actionButtons = page.locator('.mobile-action-buttons');
    await expect(actionButtons).toBeVisible();

    // Fold button visible
    await expect(page.locator('.action-btn.fold-btn')).toBeVisible();

    // Check button visible
    await expect(page.locator('.action-btn.check-btn')).toBeVisible();

    // Raise button visible
    await expect(page.locator('.action-btn.raise-btn')).toBeVisible();

    // Call button should NOT be visible
    await expect(page.locator('.action-btn.call-btn')).not.toBeVisible();
  });

  test('only all-in available: fold/all_in shows Fold and All-In buttons', async ({ page }) => {
    const ctx = await mockGamePageRoutes(page, { gameState: buildGameState(['fold', 'all_in']) });
    await navigateToGamePage(page, { mockContext: ctx });

    const actionButtons = page.locator('.mobile-action-buttons');
    await expect(actionButtons).toBeVisible();

    // Fold button visible
    await expect(page.locator('.action-btn.fold-btn')).toBeVisible();

    // All-In button visible
    await expect(page.locator('.action-btn.allin-btn')).toBeVisible();

    // Raise button should NOT be visible
    await expect(page.locator('.action-btn.raise-btn')).not.toBeVisible();
  });

  test('chat button is always present when onQuickChat provided', async ({ page }) => {
    const ctx = await mockGamePageRoutes(page, { gameState: buildGameState(['fold', 'call', 'raise']) });
    await navigateToGamePage(page, { mockContext: ctx });

    const actionButtons = page.locator('.mobile-action-buttons');
    await expect(actionButtons).toBeVisible();

    // Chat button visible
    await expect(page.locator('.action-btn.chat-btn')).toBeVisible();
  });

});
