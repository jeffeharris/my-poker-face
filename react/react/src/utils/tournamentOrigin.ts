/**
 * A multi-table tournament can be launched from two places, and backing out of
 * the standings hub should return to whichever one you came from:
 *   - the Tournament menu (`/menu/tournament`) → back to that menu
 *   - the cash lobby Main Event card/Resume bar (`/cash`) → back to the lobby
 *
 * Both entry points funnel through the same `tourney-`-prefixed game and the
 * same `/tournament` standings hub, so the game ID prefix can't tell them apart
 * (cf. utils/gameId.ts). We stash the launch origin in sessionStorage at the
 * point of entry and read it back when the hub's back button is pressed.
 * sessionStorage (not in-memory state) so it survives the full-page reloads the
 * game→hub→back chain can incur, and clears itself when the tab closes.
 */
export type TournamentOrigin = '/cash' | '/menu/tournament';

const STORAGE_KEY = 'tournamentBackTarget';

/** Default for a hub reached with no recorded origin (e.g. opened directly). */
const DEFAULT_ORIGIN: TournamentOrigin = '/menu/tournament';

/** Record where the player launched/entered the current tournament from. */
export function setTournamentOrigin(origin: TournamentOrigin): void {
  try {
    sessionStorage.setItem(STORAGE_KEY, origin);
  } catch {
    /* storage unavailable (private mode / quota) — fall back to the default */
  }
}

/** The route the tournament standings hub's back button should return to. */
export function getTournamentOrigin(): TournamentOrigin {
  try {
    const stored = sessionStorage.getItem(STORAGE_KEY);
    if (stored === '/cash' || stored === '/menu/tournament') {
      return stored;
    }
  } catch {
    /* storage unavailable — fall through to the default */
  }
  return DEFAULT_ORIGIN;
}
