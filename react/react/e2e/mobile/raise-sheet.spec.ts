import { test, expect } from '@playwright/test';
import { mockGamePageRoutes, navigateToGamePage, buildGameState } from '../helpers';

test.describe('PW-07: Mobile raise sheet — open, slider, quick bets, confirm', () => {

  test('tapping Raise button opens the raise sheet', async ({ page }) => {
    const ctx = await mockGamePageRoutes(page, { gameState: buildGameState() });
    await navigateToGamePage(page, { mockContext: ctx });

    const raiseBtn = page.getByTestId('action-btn-raise');
    await expect(raiseBtn).toBeVisible();
    await raiseBtn.click();

    const raiseSheet = page.getByTestId('raise-sheet');
    await expect(raiseSheet).toBeVisible({ timeout: 5000 });
    await expect(raiseSheet.locator('.raise-title')).toBeVisible();
  });

  test('raise sheet has Cancel and Confirm buttons', async ({ page }) => {
    const ctx = await mockGamePageRoutes(page, { gameState: buildGameState() });
    await navigateToGamePage(page, { mockContext: ctx });

    await page.getByTestId('action-btn-raise').click();

    const raiseSheet = page.getByTestId('raise-sheet');
    await expect(raiseSheet).toBeVisible({ timeout: 5000 });
    await expect(page.getByTestId('raise-cancel')).toBeVisible();
    await expect(page.getByTestId('raise-confirm')).toBeVisible();
  });

  test('raise sheet shows amount display', async ({ page }) => {
    const ctx = await mockGamePageRoutes(page, { gameState: buildGameState() });
    await navigateToGamePage(page, { mockContext: ctx });

    await page.getByTestId('action-btn-raise').click();

    const raiseSheet = page.getByTestId('raise-sheet');
    await expect(raiseSheet).toBeVisible({ timeout: 5000 });

    const amountDisplay = raiseSheet.locator('.amount-value, .amount-input');
    await expect(amountDisplay.first()).toBeVisible();
  });

  test('raise sheet shows quick bet buttons', async ({ page }) => {
    const ctx = await mockGamePageRoutes(page, { gameState: buildGameState() });
    await navigateToGamePage(page, { mockContext: ctx });

    await page.getByTestId('action-btn-raise').click();

    const raiseSheet = page.getByTestId('raise-sheet');
    await expect(raiseSheet).toBeVisible({ timeout: 5000 });

    const quickBetButtons = page.getByTestId('quick-bet-btn');
    const count = await quickBetButtons.count();
    expect(count).toBeGreaterThan(0);
  });

  test('raise sheet shows slider with min/max', async ({ page }) => {
    const ctx = await mockGamePageRoutes(page, { gameState: buildGameState() });
    await navigateToGamePage(page, { mockContext: ctx });

    await page.getByTestId('action-btn-raise').click();

    const raiseSheet = page.getByTestId('raise-sheet');
    await expect(raiseSheet).toBeVisible({ timeout: 5000 });

    const slider = page.getByTestId('raise-slider');
    await expect(slider).toBeVisible();

    const min = await slider.getAttribute('min');
    const max = await slider.getAttribute('max');
    expect(min).toBeTruthy();
    expect(max).toBeTruthy();
    expect(Number(min)).toBeGreaterThan(0);
    expect(Number(max)).toBeGreaterThan(Number(min));
  });

  test('tapping a quick bet button updates the amount', async ({ page }) => {
    const ctx = await mockGamePageRoutes(page, { gameState: buildGameState() });
    await navigateToGamePage(page, { mockContext: ctx });

    await page.getByTestId('action-btn-raise').click();

    const raiseSheet = page.getByTestId('raise-sheet');
    await expect(raiseSheet).toBeVisible({ timeout: 5000 });

    const amountDisplay = raiseSheet.locator('.amount-value, .amount-input');
    const _initialText = await amountDisplay.first().textContent() || await amountDisplay.first().inputValue().catch(() => '');

    const quickBetButtons = page.getByTestId('quick-bet-btn');
    const count = await quickBetButtons.count();
    expect(count).toBeGreaterThan(1);

    // Click the All-In button (last one)
    await quickBetButtons.nth(count - 1).click();

    const selectedBtn = raiseSheet.locator('.quick-bet-btn.selected');
    await expect(selectedBtn).toBeVisible();
  });

  test('tapping Cancel closes the raise sheet and shows action buttons', async ({ page }) => {
    const ctx = await mockGamePageRoutes(page, { gameState: buildGameState() });
    await navigateToGamePage(page, { mockContext: ctx });

    await page.getByTestId('action-btn-raise').click();

    const raiseSheet = page.getByTestId('raise-sheet');
    await expect(raiseSheet).toBeVisible({ timeout: 5000 });

    await page.getByTestId('raise-cancel').click();

    await expect(raiseSheet).not.toBeVisible({ timeout: 5000 });
    await expect(page.getByTestId('action-buttons')).toBeVisible();
    await expect(page.getByTestId('action-btn-raise')).toBeVisible();
  });

  test('tapping Confirm submits the raise action', async ({ page }) => {
    const ctx = await mockGamePageRoutes(page, { gameState: buildGameState() });
    await navigateToGamePage(page, { mockContext: ctx });

    const actionRequests: string[] = [];
    await page.route('**/api/game/*/action', route => {
      actionRequests.push(route.request().postData() || '');
      route.fulfill({ json: { success: true } });
    });

    await page.getByTestId('action-btn-raise').click();

    const raiseSheet = page.getByTestId('raise-sheet');
    await expect(raiseSheet).toBeVisible({ timeout: 5000 });

    await page.getByTestId('raise-confirm').click();

    await expect(raiseSheet).not.toBeVisible({ timeout: 5000 });
  });

  test('raise sheet shows stack preview', async ({ page }) => {
    const ctx = await mockGamePageRoutes(page, { gameState: buildGameState() });
    await navigateToGamePage(page, { mockContext: ctx });

    await page.getByTestId('action-btn-raise').click();

    const raiseSheet = page.getByTestId('raise-sheet');
    await expect(raiseSheet).toBeVisible({ timeout: 5000 });

    const stackPreview = page.getByTestId('stack-preview');
    await expect(stackPreview).toBeVisible();
    const text = await stackPreview.textContent();
    expect(text).toMatch(/stack after/i);
  });

});
