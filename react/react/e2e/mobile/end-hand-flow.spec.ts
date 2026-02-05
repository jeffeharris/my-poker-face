import { test, expect } from '@playwright/test';
import { mockGamePageRoutes, navigateToGamePage, buildGameState } from '../helpers';

/**
 * Tests for end-of-hand UI flow behavior.
 * Verifies that action buttons and active player highlighting are properly
 * suppressed during non-betting phases (EVALUATING_HAND, HAND_OVER, etc.)
 * and during run-it-out sequences.
 */
test.describe('End-of-hand UI flow', () => {

  test.describe('Action buttons visibility', () => {

    test('action buttons hidden during EVALUATING_HAND phase', async ({ page }) => {
      const gameState = buildGameState(['fold', 'call', 'raise'], {
        phase: 'EVALUATING_HAND',
        player_options: [], // Backend clears options during evaluation
      });
      const ctx = await mockGamePageRoutes(page, { gameState });
      await navigateToGamePage(page, { mockContext: ctx });

      // Action buttons container should not be visible during evaluation
      const actionButtons = page.getByTestId('action-buttons');
      await expect(actionButtons).not.toBeVisible();
    });

    test('action buttons hidden during HAND_OVER phase', async ({ page }) => {
      const gameState = buildGameState(['fold', 'call', 'raise'], {
        phase: 'HAND_OVER',
        player_options: [],
      });
      const ctx = await mockGamePageRoutes(page, { gameState });
      await navigateToGamePage(page, { mockContext: ctx });

      const actionButtons = page.getByTestId('action-buttons');
      await expect(actionButtons).not.toBeVisible();
    });

    test('action buttons hidden during SHOWDOWN phase', async ({ page }) => {
      const gameState = buildGameState(['fold', 'call', 'raise'], {
        phase: 'SHOWDOWN',
        player_options: [],
      });
      const ctx = await mockGamePageRoutes(page, { gameState });
      await navigateToGamePage(page, { mockContext: ctx });

      const actionButtons = page.getByTestId('action-buttons');
      await expect(actionButtons).not.toBeVisible();
    });

    test('action buttons hidden during GAME_OVER phase', async ({ page }) => {
      const gameState = buildGameState(['fold', 'call', 'raise'], {
        phase: 'GAME_OVER',
        player_options: [],
      });
      const ctx = await mockGamePageRoutes(page, { gameState });
      await navigateToGamePage(page, { mockContext: ctx });

      const actionButtons = page.getByTestId('action-buttons');
      await expect(actionButtons).not.toBeVisible();
    });

    test('action buttons hidden during run_it_out', async ({ page }) => {
      const gameState = buildGameState(['fold', 'call', 'raise'], {
        phase: 'RIVER',
        run_it_out: true,
        player_options: [], // Backend clears options during run-it-out
      });
      const ctx = await mockGamePageRoutes(page, { gameState });
      await navigateToGamePage(page, { mockContext: ctx });

      const actionButtons = page.getByTestId('action-buttons');
      await expect(actionButtons).not.toBeVisible();
    });

    test('action buttons visible during normal betting phase', async ({ page }) => {
      const gameState = buildGameState(['fold', 'call', 'raise'], {
        phase: 'PRE_FLOP',
        run_it_out: false,
      });
      const ctx = await mockGamePageRoutes(page, { gameState });
      await navigateToGamePage(page, { mockContext: ctx });

      const actionButtons = page.getByTestId('action-buttons');
      await expect(actionButtons).toBeVisible();
    });

  });

  test.describe('Active player highlighting', () => {

    test('no opponent has thinking class during EVALUATING_HAND phase', async ({ page }) => {
      const gameState = buildGameState([], {
        phase: 'EVALUATING_HAND',
        current_player_idx: 1, // Batman would normally be "active"
        player_options: [],
      });
      const ctx = await mockGamePageRoutes(page, { gameState });
      await navigateToGamePage(page, { mockContext: ctx });

      // No opponent should have the "thinking" class during evaluation
      const thinkingOpponents = page.locator('.mobile-opponent.thinking');
      await expect(thinkingOpponents).toHaveCount(0);
    });

    test('no opponent has thinking class during run_it_out', async ({ page }) => {
      const gameState = buildGameState([], {
        phase: 'RIVER',
        run_it_out: true,
        current_player_idx: 1, // Batman would normally be "active"
        player_options: [],
      });
      const ctx = await mockGamePageRoutes(page, { gameState });
      await navigateToGamePage(page, { mockContext: ctx });

      // No opponent should have the "thinking" class during run-it-out
      const thinkingOpponents = page.locator('.mobile-opponent.thinking');
      await expect(thinkingOpponents).toHaveCount(0);
    });

    test('no opponent has thinking class during HAND_OVER phase', async ({ page }) => {
      const gameState = buildGameState([], {
        phase: 'HAND_OVER',
        current_player_idx: 1,
        player_options: [],
      });
      const ctx = await mockGamePageRoutes(page, { gameState });
      await navigateToGamePage(page, { mockContext: ctx });

      const thinkingOpponents = page.locator('.mobile-opponent.thinking');
      await expect(thinkingOpponents).toHaveCount(0);
    });

    test('no opponent has thinking class during GAME_OVER phase', async ({ page }) => {
      const gameState = buildGameState([], {
        phase: 'GAME_OVER',
        current_player_idx: 1,
        player_options: [],
      });
      const ctx = await mockGamePageRoutes(page, { gameState });
      await navigateToGamePage(page, { mockContext: ctx });

      const thinkingOpponents = page.locator('.mobile-opponent.thinking');
      await expect(thinkingOpponents).toHaveCount(0);
    });

    test('opponent has thinking class during normal betting phase when active', async ({ page }) => {
      const gameState = buildGameState([], {
        phase: 'PRE_FLOP',
        run_it_out: false,
        current_player_idx: 1, // Batman is active
        player_options: [],
      });
      const ctx = await mockGamePageRoutes(page, { gameState });
      await navigateToGamePage(page, { mockContext: ctx });

      // Batman (index 1) should have the thinking class during normal play
      const thinkingOpponents = page.locator('.mobile-opponent.thinking');
      await expect(thinkingOpponents).toHaveCount(1);
    });

  });

});
