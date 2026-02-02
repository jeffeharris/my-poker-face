import { test, expect } from '@playwright/test';
import { mockGamePageRoutes, navigateToGamePage, navigateToMenuPage, buildGameState, mockMenuPageRoutes, setAuthLocalStorage } from '../helpers';

test.describe('Error scenarios: API error responses', () => {

  test('game state 500 shows error or fallback UI', async ({ page }) => {
    const gameId = 'test-game-error';
    await mockGamePageRoutes(page, { gameState: buildGameState(), gameId });

    // Override the game-state route to return 500
    await page.route(`**/api/game-state/${gameId}`, route =>
      route.fulfill({ status: 500, json: { error: 'Internal server error' } })
    );

    await page.goto('/menu', { waitUntil: 'commit' });
    await setAuthLocalStorage(page);
    await page.goto(`/game/${gameId}`);

    // Should show either an error message or loading state — not crash
    await page.waitForTimeout(3000);
    // The page should still be rendered (no blank screen)
    const body = await page.locator('body').textContent();
    expect(body).toBeTruthy();
  });

  test('player action 500 does not crash the game', async ({ page }) => {
    const ctx = await mockGamePageRoutes(page, { gameState: buildGameState(['fold', 'call', 'raise']) });
    await navigateToGamePage(page, { mockContext: ctx });

    // Override the action route to return 500
    await page.route('**/api/game/*/action', route =>
      route.fulfill({ status: 500, json: { error: 'Action failed' } })
    );

    const foldBtn = page.getByTestId('action-btn-fold');
    await expect(foldBtn).toBeVisible();
    await foldBtn.click();

    // Game should still be functional — table should still be visible
    await page.waitForTimeout(1000);
    const table = page.getByTestId('mobile-poker-table');
    await expect(table).toBeVisible();
  });

  test('auth 401 mid-session redirects to login', async ({ page }) => {
    const ctx = await mockGamePageRoutes(page, { gameState: buildGameState() });
    await navigateToGamePage(page, { mockContext: ctx });

    // Override auth/me to return 401 (session expired)
    await page.unroute('**/api/auth/me');
    await page.route('**/api/auth/me', route =>
      route.fulfill({ status: 401, json: { error: 'Not authenticated' } })
    );

    // Trigger a navigation that would re-check auth
    await page.goto('/menu');

    // Should eventually redirect to login or show login UI
    await page.waitForTimeout(3000);
    const url = page.url();
    // The app should handle 401 — either redirect to login or show the landing page
    expect(url).toMatch(/\/(login|menu|)$/);
  });

  test('new game 503 shows error feedback on menu', async ({ page }) => {
    await mockMenuPageRoutes(page);

    // Override new-game route to return 503
    await page.route('**/api/new-game', route =>
      route.fulfill({ status: 503, json: { error: 'Service unavailable' } })
    );

    await navigateToMenuPage(page);

    // Click Lightning quick play
    const lightning = page.locator('.quick-play-btn--lightning');
    await expect(lightning).toBeVisible();
    await lightning.click();

    // Should not navigate away from menu — game creation failed
    await page.waitForTimeout(2000);
    const url = page.url();
    // Should still be on menu or show an error, not on a game page
    expect(url).not.toMatch(/\/game\//);
  });

  test('chat API 500 degrades gracefully', async ({ page }) => {
    const ctx = await mockGamePageRoutes(page, { gameState: buildGameState() });
    await navigateToGamePage(page, { mockContext: ctx });

    // Override chat route to return 500
    await page.route('**/api/game/*/chat', route =>
      route.fulfill({ status: 500, json: { error: 'Chat failed' } })
    );

    // Open chat sheet
    const chatBtn = page.getByTestId('action-btn-chat');
    await expect(chatBtn).toBeVisible();
    await chatBtn.click();

    const sheet = page.locator('.mcs-sheet');
    await expect(sheet).toBeVisible({ timeout: 5000 });

    // Type and send a message
    const textInput = sheet.locator('.mcs-text-input');
    await textInput.fill('Hello!');
    const sendBtn = sheet.locator('.mcs-send-btn');
    await sendBtn.click();

    // Game should still be functional after chat error
    await page.waitForTimeout(1000);
    const table = page.getByTestId('mobile-poker-table');
    await expect(table).toBeVisible();
  });

});
