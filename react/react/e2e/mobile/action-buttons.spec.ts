import { test, expect } from '@playwright/test';
import { mockGamePageRoutes, navigateToGamePage, buildGameState } from '../helpers';

test.describe('PW-06: Mobile action buttons display correct options per game state', () => {

  test('pre-flop with fold/call/raise shows Fold, Call, Raise buttons', async ({ page }) => {
    const ctx = await mockGamePageRoutes(page, { gameState: buildGameState(['fold', 'call', 'raise']) });
    await navigateToGamePage(page, { mockContext: ctx });

    const actionButtons = page.getByTestId('action-buttons');
    await expect(actionButtons).toBeVisible();

    // Fold button visible
    await expect(page.getByTestId('action-btn-fold')).toBeVisible();

    // Call button visible with amount text
    const callBtn = page.getByTestId('action-btn-call');
    await expect(callBtn).toBeVisible();

    // Raise button visible
    await expect(page.getByTestId('action-btn-raise')).toBeVisible();

    // Check button should NOT be visible
    await expect(page.getByTestId('action-btn-check')).not.toBeVisible();
  });

  test('big blind option: fold/check/raise shows Fold, Check, Raise buttons', async ({ page }) => {
    const ctx = await mockGamePageRoutes(page, { gameState: buildGameState(['fold', 'check', 'raise']) });
    await navigateToGamePage(page, { mockContext: ctx });

    const actionButtons = page.getByTestId('action-buttons');
    await expect(actionButtons).toBeVisible();

    // Fold button visible
    await expect(page.getByTestId('action-btn-fold')).toBeVisible();

    // Check button visible
    await expect(page.getByTestId('action-btn-check')).toBeVisible();

    // Raise button visible
    await expect(page.getByTestId('action-btn-raise')).toBeVisible();

    // Call button should NOT be visible
    await expect(page.getByTestId('action-btn-call')).not.toBeVisible();
  });

  test('only all-in available: fold/all_in shows Fold and All-In buttons', async ({ page }) => {
    const ctx = await mockGamePageRoutes(page, { gameState: buildGameState(['fold', 'all_in']) });
    await navigateToGamePage(page, { mockContext: ctx });

    const actionButtons = page.getByTestId('action-buttons');
    await expect(actionButtons).toBeVisible();

    // Fold button visible
    await expect(page.getByTestId('action-btn-fold')).toBeVisible();

    // All-In button visible
    await expect(page.getByTestId('action-btn-allin')).toBeVisible();

    // Raise button should NOT be visible
    await expect(page.getByTestId('action-btn-raise')).not.toBeVisible();
  });

  test('chat button is always present when onQuickChat provided', async ({ page }) => {
    const ctx = await mockGamePageRoutes(page, { gameState: buildGameState(['fold', 'call', 'raise']) });
    await navigateToGamePage(page, { mockContext: ctx });

    const actionButtons = page.getByTestId('action-buttons');
    await expect(actionButtons).toBeVisible();

    // Chat button visible
    await expect(page.getByTestId('action-btn-chat')).toBeVisible();
  });

});
