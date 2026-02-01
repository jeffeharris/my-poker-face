import { test, expect } from '@playwright/test';
import { mockGamePageRoutes, navigateToMenuPage, buildGameState } from '../helpers';

test.describe('PW-04: Quick Play Lightning creates game and mobile table loads', () => {

  test.beforeEach(async ({ page }) => {
    await mockGamePageRoutes(page, { gameState: buildGameState() });
    await navigateToMenuPage(page);

    // Click Lightning to create game and navigate to game page
    const lightning = page.locator('.quick-play-btn--lightning');
    await expect(lightning).toBeVisible();
    await lightning.click();
    await page.waitForURL('**/game/test-game-123', { timeout: 10000 });
  });

  test('game creation triggers navigation to game page', async ({ page }) => {
    await expect(page).toHaveURL(/\/game\/test-game-123/);
  });

  test('mobile poker table renders with opponents', async ({ page }) => {
    const table = page.locator('.mobile-poker-table');
    await expect(table).toBeVisible({ timeout: 10000 });

    const opponents = page.locator('.mobile-opponents');
    await expect(opponents).toBeVisible();

    await expect(page.locator('.opponent-name').first()).toBeVisible();
  });

  test('community cards area visible', async ({ page }) => {
    const table = page.locator('.mobile-poker-table');
    await expect(table).toBeVisible({ timeout: 10000 });

    const community = page.locator('.mobile-community');
    await expect(community).toBeVisible();
  });

  test('hero section with player cards visible', async ({ page }) => {
    const table = page.locator('.mobile-poker-table');
    await expect(table).toBeVisible({ timeout: 10000 });

    const hero = page.locator('.mobile-hero');
    await expect(hero).toBeVisible();

    const heroCards = page.locator('.hero-cards');
    await expect(heroCards).toBeVisible();
  });

  test('action buttons visible (fold, call, raise)', async ({ page }) => {
    const table = page.locator('.mobile-poker-table');
    await expect(table).toBeVisible({ timeout: 10000 });

    const actionButtons = page.locator('.mobile-action-buttons');
    await expect(actionButtons).toBeVisible();

    const foldBtn = page.locator('.action-btn.fold-btn');
    await expect(foldBtn).toBeVisible();

    const callBtn = page.locator('.action-btn.call-btn');
    await expect(callBtn).toBeVisible();

    const raiseBtn = page.locator('.action-btn.raise-btn');
    await expect(raiseBtn).toBeVisible();
  });

  test('pot display shows correct amount', async ({ page }) => {
    const table = page.locator('.mobile-poker-table');
    await expect(table).toBeVisible({ timeout: 10000 });

    const pot = page.locator('.mobile-pot');
    await expect(pot).toBeVisible();
    await expect(pot).toContainText('150');
  });
});
