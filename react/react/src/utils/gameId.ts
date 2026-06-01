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

/** Training-mode game IDs are prefixed `train-` by the backend
 *  (`/api/training/start`). Training games are non-counting practice
 *  sessions; the frontend keys back-navigation off the same prefix,
 *  mirroring cash. */
export const TRAINING_GAME_ID_PREFIX = 'train-';

/** True when `gameId` belongs to a training/practice session. */
export function isTrainingGameId(gameId: string | null | undefined): boolean {
  return gameId?.startsWith(TRAINING_GAME_ID_PREFIX) ?? false;
}
