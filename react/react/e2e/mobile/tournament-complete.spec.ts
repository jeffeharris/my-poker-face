import { test, expect } from '@playwright/test';
import { mockGamePageRoutes, navigateToGamePage, buildGameState } from '../helpers';

const initialGameState = buildGameState();

/** Tournament result fixture: TestPlayer wins (1st place) */
const tournamentResultWinner = {
  winner: 'TestPlayer',
  standings: [
    { player_name: 'TestPlayer', is_human: true, finishing_position: 1, eliminated_by: null, eliminated_at_hand: null },
    { player_name: 'Batman', is_human: false, finishing_position: 2, eliminated_by: 'TestPlayer', eliminated_at_hand: 28 },
    { player_name: 'Gandalf', is_human: false, finishing_position: 3, eliminated_by: 'Batman', eliminated_at_hand: 15 },
  ],
  total_hands: 28,
  biggest_pot: 4500,
  human_position: 1,
  game_id: 'test-game-123',
};

/** Tournament result fixture: TestPlayer eliminated (2nd place) */
const tournamentResultEliminated = {
  winner: 'Batman',
  standings: [
    { player_name: 'Batman', is_human: false, finishing_position: 1, eliminated_by: null, eliminated_at_hand: null },
    { player_name: 'TestPlayer', is_human: true, finishing_position: 2, eliminated_by: 'Batman', eliminated_at_hand: 28 },
    { player_name: 'Gandalf', is_human: false, finishing_position: 3, eliminated_by: 'Batman', eliminated_at_hand: 15 },
  ],
  total_hands: 28,
  biggest_pot: 4500,
  human_position: 2,
  game_id: 'test-game-123',
};

async function setupWithTournament(
  page: import('@playwright/test').Page,
  tournamentPayload: Record<string, unknown>
) {
  await mockGamePageRoutes(page, {
    gameState: initialGameState,
    socketEvents: [
      ['update_game_state', { game_state: initialGameState }],
      ['tournament_complete', tournamentPayload],
    ],
  });
  await navigateToGamePage(page);
}

test.describe('PW-13: Tournament complete screen displays final standings', () => {

  test('tournament complete screen renders when player wins', async ({ page }) => {
    await setupWithTournament(page, tournamentResultWinner);

    const overlay = page.locator('.tournament-complete');
    await expect(overlay).toBeVisible({ timeout: 15000 });

    const title = overlay.locator('.tournament-title');
    await expect(title).toBeVisible();
    const titleText = await title.textContent();
    expect(titleText).toContain('CHAMPION!');

    const winnerAnnouncement = overlay.locator('.winner-announcement');
    await expect(winnerAnnouncement).toBeVisible();
    const announcementText = await winnerAnnouncement.textContent();
    expect(announcementText).toContain('TestPlayer');
    expect(announcementText).toContain('wins the tournament');
  });

  test('player finishing position is displayed', async ({ page }) => {
    await setupWithTournament(page, tournamentResultWinner);

    const overlay = page.locator('.tournament-complete');
    await expect(overlay).toBeVisible({ timeout: 15000 });

    const yourResult = overlay.locator('.your-result');
    await expect(yourResult).toBeVisible();

    const resultPosition = overlay.locator('.result-position');
    await expect(resultPosition).toBeVisible();
    const positionText = await resultPosition.textContent();
    expect(positionText).toContain('1st');

    await expect(yourResult).toHaveClass(/winner/);
  });

  test('final standings table shows all players in order', async ({ page }) => {
    await setupWithTournament(page, tournamentResultWinner);

    const overlay = page.locator('.tournament-complete');
    await expect(overlay).toBeVisible({ timeout: 15000 });

    const standingsSection = overlay.locator('.standings-section');
    await expect(standingsSection).toBeVisible();

    const standingsTitle = overlay.locator('.standings-title');
    await expect(standingsTitle).toContainText('Final Standings');

    const standingRows = overlay.locator('.standing-row');
    await expect(standingRows).toHaveCount(3);

    const firstRow = standingRows.nth(0);
    await expect(firstRow.locator('.position')).toContainText('1st');
    await expect(firstRow.locator('.name')).toContainText('TestPlayer');
    await expect(firstRow).toHaveClass(/winner/);
    await expect(firstRow).toHaveClass(/human/);

    const secondRow = standingRows.nth(1);
    await expect(secondRow.locator('.position')).toContainText('2nd');
    await expect(secondRow.locator('.name')).toContainText('Batman');

    const thirdRow = standingRows.nth(2);
    await expect(thirdRow.locator('.position')).toContainText('3rd');
    await expect(thirdRow.locator('.name')).toContainText('Gandalf');
  });

  test('tournament stats show hands played and biggest pot', async ({ page }) => {
    await setupWithTournament(page, tournamentResultWinner);

    const overlay = page.locator('.tournament-complete');
    await expect(overlay).toBeVisible({ timeout: 15000 });

    const stats = overlay.locator('.tournament-stats');
    await expect(stats).toBeVisible();

    const statValues = overlay.locator('.stat-value');
    const statLabels = overlay.locator('.stat-label');

    await expect(statValues.nth(0)).toContainText('28');
    await expect(statLabels.nth(0)).toContainText('Hands Played');

    const biggestPotText = await statValues.nth(1).textContent();
    expect(biggestPotText).toContain('4,500');
    await expect(statLabels.nth(1)).toContainText('Biggest Pot');
  });

  test('return to menu button is visible', async ({ page }) => {
    await setupWithTournament(page, tournamentResultWinner);

    const overlay = page.locator('.tournament-complete');
    await expect(overlay).toBeVisible({ timeout: 15000 });

    const continueBtn = overlay.locator('.continue-button');
    await expect(continueBtn).toBeVisible();
    const btnText = await continueBtn.textContent();
    expect(btnText).toContain('Return to Menu');
  });

  test('eliminated player sees correct title and position', async ({ page }) => {
    await setupWithTournament(page, tournamentResultEliminated);

    const overlay = page.locator('.tournament-complete');
    await expect(overlay).toBeVisible({ timeout: 15000 });

    const title = overlay.locator('.tournament-title');
    const titleText = await title.textContent();
    expect(titleText).not.toContain('CHAMPION');

    const winnerAnnouncement = overlay.locator('.winner-announcement');
    await expect(winnerAnnouncement).toBeVisible();
    const announcementText = await winnerAnnouncement.textContent();
    expect(announcementText).toContain('Batman');

    const resultPosition = overlay.locator('.result-position');
    await expect(resultPosition).toBeVisible();
    const positionText = await resultPosition.textContent();
    expect(positionText).toContain('2nd');

    const eliminatedBy = overlay.locator('.eliminated-by');
    await expect(eliminatedBy).toBeVisible();
    const eliminatedText = await eliminatedBy.textContent();
    expect(eliminatedText).toContain('Batman');
  });

  test('elimination details shown in standings', async ({ page }) => {
    await setupWithTournament(page, tournamentResultEliminated);

    const overlay = page.locator('.tournament-complete');
    await expect(overlay).toBeVisible({ timeout: 15000 });

    const standingRows = overlay.locator('.standing-row');
    await expect(standingRows).toHaveCount(3);

    const winnerRow = standingRows.nth(0);
    await expect(winnerRow.locator('.eliminated-info')).toContainText('Winner');

    const secondRow = standingRows.nth(1);
    await expect(secondRow.locator('.eliminated-info')).toContainText('by Batman');

    const thirdRow = standingRows.nth(2);
    await expect(thirdRow.locator('.eliminated-info')).toContainText('by Batman');
  });

});
