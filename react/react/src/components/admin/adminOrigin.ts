/**
 * Tracks where the user entered the admin area from, so the admin "back"
 * affordance can return them to that exact place — the game they were
 * playing, the cash lobby, a menu — instead of relying on the browser
 * history stack (which breaks across hard navigations and lateral hops).
 *
 * Backed by sessionStorage so it survives both the analyzer/settings
 * `history.replaceState` URL tricks AND a full page reload, and is scoped
 * to the current tab. Kept dependency-free on purpose: entry points in the
 * main bundle (App, Lobby, the game views) import this without pulling in
 * the heavy lazy-loaded admin chunk.
 */
const ORIGIN_KEY = 'admin:returnTo';
const DEFAULT_ORIGIN = '/menu';

/** Record the path to return to when the user leaves admin. No-op for admin
 *  paths themselves, so navigating around inside admin never overwrites the
 *  original origin. */
export function rememberAdminOrigin(path: string): void {
  try {
    if (path && !path.startsWith('/admin')) {
      sessionStorage.setItem(ORIGIN_KEY, path);
    }
  } catch {
    // sessionStorage can throw in private mode / sandboxed iframes —
    // getAdminOrigin() then falls back to the menu.
  }
}

/** The path to return to when exiting admin; falls back to the main menu. */
export function getAdminOrigin(): string {
  try {
    return sessionStorage.getItem(ORIGIN_KEY) || DEFAULT_ORIGIN;
  } catch {
    return DEFAULT_ORIGIN;
  }
}
