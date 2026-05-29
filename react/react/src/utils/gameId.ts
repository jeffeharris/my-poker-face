/** Cash-mode game IDs are prefixed `cash-` by the backend (an established
 *  convention used at 6+ backend sites — game creation, `_find_active_cash_
 *  game_id`, cold-load recovery). The frontend keys cash-vs-tournament
 *  back-navigation and 404 recovery off the same prefix, so this FE/BE
 *  contract lives in one place instead of scattering the string literal. */
export const CASH_GAME_ID_PREFIX = 'cash-';

/** True when `gameId` belongs to a cash-mode session. */
export function isCashGameId(gameId: string | null | undefined): boolean {
  return gameId?.startsWith(CASH_GAME_ID_PREFIX) ?? false;
}

/** Multi-table tournament live tables are prefixed `tourney-` by the backend
 *  (tournament_game_builder). The human's seat plays as an ordinary game at
 *  /game/:id; back-navigation routes to the tournament standings hub. */
export const TOURNAMENT_GAME_ID_PREFIX = 'tourney-';

/** True when `gameId` is a multi-table tournament live table. */
export function isTournamentGameId(gameId: string | null | undefined): boolean {
  return gameId?.startsWith(TOURNAMENT_GAME_ID_PREFIX) ?? false;
}
