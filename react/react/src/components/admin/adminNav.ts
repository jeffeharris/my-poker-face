import { SIDEBAR_ITEMS } from './adminSidebarItems';

/**
 * The declared admin navigation hierarchy — the single source of truth for
 * both the desktop breadcrumb trail and the (desktop + mobile) back-arrow.
 *
 * "Back" is deterministic: it walks one step UP this hierarchy regardless of
 * browser history, so lateral tab-hops never pollute it. At the admin root,
 * "up" means "exit", handed off to {@link getAdminOrigin}.
 */

export interface Crumb {
  label: string;
  /** Absolute path to navigate to. `null` marks the current (leaf) crumb. */
  path: string | null;
}

const TAB_LABELS: Record<string, string> = Object.fromEntries(
  SIDEBAR_ITEMS.map((item) => [item.id, item.label])
);

function tabLabel(tab: string): string {
  return TAB_LABELS[tab] ?? 'Admin';
}

function segments(pathname: string): string[] {
  // '/admin/analyzer/42/' -> ['admin', 'analyzer', '42']
  return pathname.replace(/\/+$/, '').split('/').filter(Boolean);
}

/**
 * The deterministic parent of an admin location — one step "up" the nav path.
 * Returns `null` at the admin root (`/admin`), signalling "exit to origin".
 */
export function getAdminParent(pathname: string): string | null {
  const parts = segments(pathname);
  if (parts.length <= 1) return null; // '/admin' (or '/') -> root

  const [, section, sub] = parts; // parts[0] === 'admin'

  // Detail views hang off a list/tool parent.
  if (section === 'analyzer' && sub) return '/admin/analyzer';
  if (section === 'experiments' && sub) return '/admin/experiments'; // :id or 'new'
  if (section === 'replays' && sub) return '/admin/experiments';
  if (section === 'settings' && sub) return '/admin'; // category is lateral within Settings

  // A bare tool (`/admin/:tab`) sits directly under the menu.
  return '/admin';
}

/**
 * Build the breadcrumb trail for an admin location.
 *
 * `leafLabel` lets a screen supply the current leaf's text from component
 * state rather than the URL — required for the Decision Analyzer, whose
 * selection updates the URL via `history.replaceState` (which `useLocation`
 * never observes, so the param would be stale). Pass `null` to explicitly
 * drop the leaf crumb (e.g. the analyzer selection was cleared).
 */
export function buildAdminTrail(pathname: string, opts?: { leafLabel?: string | null }): Crumb[] {
  const parts = segments(pathname);

  // Root: "Admin" is the current page.
  if (parts.length <= 1) {
    return [{ label: 'Admin', path: null }];
  }

  const trail: Crumb[] = [{ label: 'Admin', path: '/admin' }];
  const section = parts[1];
  const sub = parts[2];
  const leaf = opts?.leafLabel;

  if (section === 'experiments') {
    if (sub === 'new') {
      trail.push({ label: 'Experiments', path: '/admin/experiments' });
      trail.push({ label: 'New', path: null });
    } else if (sub) {
      trail.push({ label: 'Experiments', path: '/admin/experiments' });
      trail.push({ label: leaf ?? `#${sub}`, path: null });
    } else {
      trail.push({ label: 'Experiments', path: null });
    }
  } else if (section === 'replays') {
    trail.push({ label: 'Experiments', path: '/admin/experiments' });
    trail.push({ label: leaf ?? `Replay #${sub}`, path: null });
  } else if (section === 'analyzer') {
    // leaf === null -> selection cleared, analyzer list is current.
    if (leaf === null || !sub) {
      trail.push({ label: 'Decision Analyzer', path: null });
    } else {
      trail.push({ label: 'Decision Analyzer', path: '/admin/analyzer' });
      trail.push({ label: leaf ?? `Capture #${sub}`, path: null });
    }
  } else if (section === 'settings') {
    // The category is a lateral sub-tab within Settings, not a deeper level.
    trail.push({ label: 'Settings', path: null });
  } else {
    // Generic tool tab (`/admin/:tab`).
    trail.push({ label: tabLabel(section), path: null });
  }

  return trail;
}
