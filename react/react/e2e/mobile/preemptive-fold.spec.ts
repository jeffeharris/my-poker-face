import { test, expect } from '@playwright/test';
import { mockGamePageRoutes, navigateToGamePage, buildGameState } from '../helpers';

/**
 * Build a game state where it's an AI player's turn (not the human's).
 * current_player_idx=1 means Batman is thinking. Human has no player_options.
 */
function buildWaitingGameState(extraOverrides: Record<string, unknown> = {}) {
  return buildGameState([], {
    current_player_idx: 1,
    ...extraOverrides,
  });
}

test.describe('PW-08: Preemptive fold while waiting for opponent', () => {

  test('shows waiting text when it is not the human player\'s turn', async ({ page }) => {
    await mockGamePageRoutes(page, { gameState: buildWaitingGameState() });
    await navigateToGamePage(page);

    const waitingText = page.locator('.waiting-text');
    await expect(waitingText).toBeVisible({ timeout: 5000 });
    await expect(waitingText).toContainText(/thinking|waiting/i);
  });

  test('preemptive fold button is available during wait', async ({ page }) => {
    await mockGamePageRoutes(page, { gameState: buildWaitingGameState() });
    await navigateToGamePage(page);

    const preemptiveBtn = page.locator('.action-btn.preemptive-btn');
    await expect(preemptiveBtn).toBeVisible({ timeout: 5000 });
    await expect(preemptiveBtn).toContainText(/chk\/fold/i);
  });

  test('tapping preemptive fold queues the action and shows queued state', async ({ page }) => {
    await mockGamePageRoutes(page, { gameState: buildWaitingGameState() });
    await navigateToGamePage(page);

    const preemptiveBtn = page.locator('.action-btn.preemptive-btn');
    await expect(preemptiveBtn).toBeVisible({ timeout: 5000 });

    await preemptiveBtn.click();

    await expect(preemptiveBtn).toHaveClass(/queued/);
    await expect(preemptiveBtn).toContainText(/queued/i);
  });

  test('tapping queued preemptive fold again dequeues the action', async ({ page }) => {
    await mockGamePageRoutes(page, { gameState: buildWaitingGameState() });
    await navigateToGamePage(page);

    const preemptiveBtn = page.locator('.action-btn.preemptive-btn');
    await expect(preemptiveBtn).toBeVisible({ timeout: 5000 });

    await preemptiveBtn.click();
    await expect(preemptiveBtn).toHaveClass(/queued/);

    await preemptiveBtn.click();
    await expect(preemptiveBtn).not.toHaveClass(/queued/);
    await expect(preemptiveBtn).toContainText(/chk\/fold/i);
  });

  test('chat button remains available during waiting state', async ({ page }) => {
    await mockGamePageRoutes(page, { gameState: buildWaitingGameState() });
    await navigateToGamePage(page);

    const chatBtn = page.locator('.action-btn.chat-btn');
    await expect(chatBtn).toBeVisible({ timeout: 5000 });
  });

  test('standard action buttons are not visible when waiting for opponent', async ({ page }) => {
    await mockGamePageRoutes(page, { gameState: buildWaitingGameState() });
    await navigateToGamePage(page);

    await expect(page.locator('.waiting-text')).toBeVisible({ timeout: 5000 });

    await expect(page.locator('.action-btn.fold-btn')).not.toBeVisible();
    await expect(page.locator('.action-btn.call-btn')).not.toBeVisible();
    await expect(page.locator('.action-btn.raise-btn')).not.toBeVisible();
  });

});
