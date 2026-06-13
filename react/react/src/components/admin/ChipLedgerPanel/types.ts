// ============================================
// ChipLedgerPanel — shared types
// ============================================

export interface LedgerTotals {
  chips_created: number;
  chips_destroyed: number;
  outstanding: number;
}

export interface ActualTotals {
  player_bankrolls: number;
  ai_bankrolls_stored: number;
  ai_bankrolls_projected: number;
  uncommitted_ai_regen: number;
  cash_table_seats_ai: number;
  active_loans_principal: number;
  live_session_ai_stacks: number;
  actual_outstanding: number;
}

export interface BankPool {
  reserves: number;
  deposits_total: number;
  draws_total: number;
  deposits_24h: number;
  draws_24h: number;
  net_flow_24h: number;
  deposit_reasons: string[];
  draw_reasons: string[];
}

export interface AuditResponse {
  ledger_totals: LedgerTotals;
  actual_totals: ActualTotals;
  drift: number;
  bank_pool?: BankPool;
  by_reason: Record<string, number>;
  by_reason_window_24h: Record<string, number>;
  errors?: Record<string, string>;
  as_of: string;
  /** World ticks for the selected sandbox (summed across all when unscoped).
   *  A maturity gauge — concentration reads differently at 50 vs 5,000 ticks. */
  world_ticks?: number | null;
}

export interface LedgerEntry {
  entry_id: number;
  created_at: string;
  source: string;
  sink: string;
  amount: number;
  reason: string;
  context?: Record<string, unknown> | null;
}

export interface SandboxRow {
  sandbox_id: string;
  owner_id: string;
  name: string;
  created_at: string;
}

export interface HoldingsRow {
  entity_id: string;
  kind: 'ai' | 'player';
  id: string;
  name: string;
  sandbox_id: string | null;
  stored_chips: number;
  projected_chips: number;
  // Total chips controlled = projected bankroll + chips in play on a
  // cash-table seat. `seat_chips` is the in-play portion (scoped only).
  chips: number;
  seat_chips?: number;
  uncommitted_regen: number;
  last_regen_tick: string | null;
  // Net-worth block — present only in the scoped (single-sandbox) view.
  // net_worth = chips (incl. in-play) + receivable − outstanding.
  net_worth?: number;
  receivable?: number;
  outstanding?: number;
  staking_pnl?: number; // realized P&L from backing others (signed)
  vice_spent?: number;
  side_hustle_earned?: number;
  rake_paid?: number; // total rake contributed to the house
}

export interface HoldingsSnapshotResponse {
  rows: HoldingsRow[];
  as_of: string;
  sandbox_id: string | null;
  // False in the cross-sandbox "All sandboxes" view → net worth omitted
  // (stakes are global per entity; chips are per-sandbox).
  net_worth_scoped: boolean;
}

export interface HoldingsSeriesPoint {
  t: string;
  value: number; // net worth at this timestamp
}

export interface HoldingsSeries {
  entity_id: string;
  label: string;
  kind: 'ai' | 'player';
  current_net_worth: number;
  points: HoldingsSeriesPoint[];
}

export interface HoldingsHistoryResponse {
  series: HoldingsSeries[];
  series_total: number;
  series_truncated_to: number;
  since: string;
  as_of: string;
  sandbox_id: string | null;
  days: number;
  // True when no sandbox is selected — net worth needs one, so the chart
  // shows a "select a sandbox" prompt instead of an empty plot.
  requires_sandbox: boolean;
}

// --- Economy chairman (the Director thermostat) -----------------------------

export interface ChairmanLevers {
  rake: { tiers: number[]; rate: number };
  vice_multiplier: number;
  tournament_armed: boolean;
}

export interface ChairmanBand {
  key: 'critical' | 'low' | 'climbing' | 'trigger';
  label: string;
  ratio_min: number;
  ratio_max: number | null; // null → open-topped trigger band
  blurb: string;
  levers: ChairmanLevers;
}

export interface ChairmanResponse {
  sandbox_id: string | null;
  signal: {
    reserves: number;
    holdings: number;
    ratio: number;
    regime: 'flush' | 'neutral' | 'empty';
  };
  thresholds: {
    critical: number;
    healthy: number;
    trigger: number;
    vice_ceiling: number;
  };
  // null when the economy is cold (no chips yet) — the bands carry no signal.
  current_band: ChairmanBand['key'] | null;
  bands: ChairmanBand[];
  levers: ChairmanLevers | null;
  policy_lock: {
    hold_enabled: boolean;
    window_seconds: number;
    tournament_cooldown_seconds: number;
    registration_window_seconds: number;
    last_computed: string | null;
    seconds_remaining: number | null;
  };
  as_of: string;
}

export interface LifecycleResponse {
  window_hours: number;
  // event name → count over the window (started/left_clean/left_ghost/swept/broken/...)
  events: Record<string, number>;
  // session_state → current count (active/paused/closed/broken/...)
  states: Record<string, number>;
  outstanding_broken: number;
}

export interface ChipLedgerPanelProps {
  embedded?: boolean;
}

export type SortKey =
  | 'name'
  | 'kind'
  | 'chips'
  | 'sandbox_id'
  | 'net_worth'
  | 'receivable'
  | 'outstanding'
  | 'staking_pnl'
  | 'vice_spent'
  | 'side_hustle_earned'
  | 'rake_paid';
export type SortDir = 'asc' | 'desc';
