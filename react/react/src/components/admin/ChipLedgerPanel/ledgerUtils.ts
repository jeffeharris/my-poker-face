import type { HoldingsRow, SortKey, SortDir } from './types';

// ============================================
// Constants
// ============================================

export const HISTORY_DAYS_OPTIONS = [7, 30, 90] as const;
export const CHART_TOP_N = 8;
export const CHART_HEIGHT = 240;
export const CHART_PAD_LEFT = 56;
export const CHART_PAD_RIGHT = 12;
export const CHART_PAD_TOP = 12;
export const CHART_PAD_BOTTOM = 28;

// Hand-picked palette for the multi-line chart. Mirrors the
// `chip-ledger-card` dark theme tokens — bright enough to read on
// near-black, distinct enough to tell 8 lines apart at a glance.
export const CHART_COLORS = [
  '#d4a574',
  '#56d364',
  '#79b8ff',
  '#f6a5c0',
  '#f5d76e',
  '#a78bfa',
  '#5cdbd3',
  '#ff8c5a',
  '#b8b8b8',
  '#73c991',
];

export const REFRESH_MS = 30_000;
export const ALL_SANDBOXES = ''; // sentinel for the cross-sandbox admin view

// Scoped-only columns: meaningless (absent) in the All-sandboxes view.
export const NET_WORTH_KEYS: ReadonlySet<SortKey> = new Set<SortKey>([
  'net_worth',
  'receivable',
  'outstanding',
  'staking_pnl',
  'vice_spent',
  'side_hustle_earned',
  'rake_paid',
]);
export const STRING_KEYS: ReadonlySet<SortKey> = new Set<SortKey>(['name', 'kind', 'sandbox_id']);

// Friendly labels for ledger reason codes. Unmapped reasons fall back to
// the raw code so a newly-added reason never disappears from the UI.
const REASON_LABELS: Record<string, string> = {
  player_seed: 'Player seed',
  ai_seed: 'AI seed',
  ai_regen: 'AI regen (passive, retired)',
  house_stake_issue: 'House stake issued',
  tourist_injection: 'Tourist injection',
  casino_seat_seed: 'Casino seat seed',
  side_hustle_earning: 'Side hustle',
  bank_pool_sim_seed: 'Bank pool sim seed',
  cap_clamp: 'Cap clamp (legacy)',
  house_stake_settle: 'House stake settle',
  table_rake: 'Table rake',
  bank_pool_deposit: 'Bank pool deposit',
  vice_spending: 'Vice spending',
  casino_seat_return: 'Casino seat return',
  forgive_balance: 'Forgive balance',
};

// ============================================
// Formatters / helpers
// ============================================

export function fmt(n: number | undefined | null): string {
  if (n === undefined || n === null || Number.isNaN(n)) return '—';
  return n.toLocaleString();
}

export function signed(n: number | undefined | null): string {
  if (n === undefined || n === null || Number.isNaN(n)) return '—';
  return n > 0 ? `+${fmt(n)}` : fmt(n);
}

export function labelFor(reason: string): string {
  return REASON_LABELS[reason] ?? reason;
}

export function compareRows(a: HoldingsRow, b: HoldingsRow, key: SortKey, dir: SortDir): number {
  // Coerce undefined (absent net-worth fields) and null to a sentinel that
  // sorts to the bottom regardless of direction.
  const av = a[key] ?? null;
  const bv = b[key] ?? null;
  if (av === null && bv === null) return 0;
  if (av === null) return 1;
  if (bv === null) return -1;
  let cmp: number;
  if (typeof av === 'number' && typeof bv === 'number') {
    cmp = av - bv;
  } else {
    cmp = String(av).localeCompare(String(bv));
  }
  return dir === 'asc' ? cmp : -cmp;
}

export function computeYTicks(yMin: number, yMax: number): number[] {
  // Four ticks across the range, rounded to a "nice" step so they
  // land on round numbers instead of arbitrary decimals.
  const span = yMax - yMin;
  if (span <= 0) return [yMin];
  const rough = span / 4;
  const mag = Math.pow(10, Math.floor(Math.log10(Math.abs(rough))));
  const step = Math.ceil(rough / mag) * mag;
  const start = Math.floor(yMin / step) * step;
  const ticks: number[] = [];
  for (let t = start; t <= yMax + step / 2; t += step) {
    ticks.push(Math.round(t));
  }
  return ticks;
}
