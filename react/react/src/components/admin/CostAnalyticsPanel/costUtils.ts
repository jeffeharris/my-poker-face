import type { CostRange } from './types';

// Core panels refresh on this cadence. api_usage aggregations are cheap-ish
// SQLite SUMs and spend moves slowly; 60s keeps the view live without
// hammering the single prod worker. Polling pauses when the tab is hidden
// (see useVisiblePolling).
export const REFRESH_MS = 60_000;

export const RANGE_OPTIONS: { value: CostRange; label: string }[] = [
  { value: '24h', label: '24h' },
  { value: '7d', label: '7d' },
  { value: '30d', label: '30d' },
  { value: 'all', label: 'All' },
];

// Shared palette for call-type / model slices. Bright on near-black,
// distinct enough to tell ~10 categories apart at a glance. Mirrors the
// chip-ledger chart palette so the admin surface stays cohesive.
export const CHART_COLORS = [
  '#56d364',
  '#79b8ff',
  '#d4a574',
  '#f6a5c0',
  '#f5d76e',
  '#a78bfa',
  '#5cdbd3',
  '#ff8c5a',
  '#b8b8b8',
  '#73c991',
  '#e85d75',
  '#62b6f7',
];

// Friendly labels for CallType enum values. Unmapped types fall back to a
// title-cased version of the raw code, so a newly-added call type never
// silently disappears from the legend.
const CALL_TYPE_LABELS: Record<string, string> = {
  player_decision: 'Player decision',
  commentary: 'Commentary',
  chat_suggestion: 'Chat suggestion',
  targeted_chat: 'Targeted chat',
  post_round_chat: 'Post-round chat',
  personality_generation: 'Personality gen',
  personality_preview: 'Personality preview',
  theme_generation: 'Theme gen',
  image_generation: 'Image generation',
  image_description: 'Image description',
  categorization: 'Categorization',
  narration_cleanup: 'Narration cleanup',
  journey_narration: 'Journey narration',
  vice_narration: 'Vice narration',
  side_hustle_narration: 'Side-hustle narration',
  debug_replay: 'Debug replay',
  debug_interrogate: 'Debug interrogate',
  experiment_design: 'Experiment design',
  experiment_analysis: 'Experiment analysis',
  coaching: 'Coaching',
  unknown: 'Unknown',
};

export function callTypeLabel(callType: string): string {
  return (
    CALL_TYPE_LABELS[callType] ?? callType.replace(/_/g, ' ').replace(/^\w/, (c) => c.toUpperCase())
  );
}

/** USD formatter. Sub-cent costs get more precision so they don't read as $0.00. */
export function fmtCost(n: number | null | undefined): string {
  if (n == null) return '—';
  if (n === 0) return '$0.00';
  if (Math.abs(n) < 0.01) return `$${n.toFixed(5)}`;
  if (Math.abs(n) < 1) return `$${n.toFixed(4)}`;
  return `$${n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

/** Compact integer formatter: 1234567 -> "1.2M". */
export function fmtCount(n: number | null | undefined): string {
  if (n == null) return '—';
  const abs = Math.abs(n);
  if (abs >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (abs >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return n.toLocaleString();
}

export function fmtTokens(n: number | null | undefined): string {
  return fmtCount(n);
}

/** Per-call average, guarding divide-by-zero. */
export function avgCostPerCall(totalCost: number, totalCalls: number): number {
  return totalCalls > 0 ? totalCost / totalCalls : 0;
}

/** Trim a long owner_id (e.g. experiment ids) for axis labels. */
export function shortOwner(ownerId: string, max = 22): string {
  return ownerId.length > max ? `${ownerId.slice(0, max - 1)}…` : ownerId;
}

/** Game ids are long; keep the trailing, most-distinguishing slice. */
export function shortGame(gameId: string, max = 32): string {
  return gameId.length > max ? `…${gameId.slice(gameId.length - max + 1)}` : gameId;
}
