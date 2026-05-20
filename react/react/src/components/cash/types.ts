/**
 * Wire-format types matching `flask_app/routes/cash_routes.py:_serialize_session`.
 *
 * Keep in sync with backend serialization — these are the only types
 * the cash mode UI consumes, so a drift would surface as a runtime
 * error rather than a type error.
 */

export type StakeLabel = '$2' | '$10' | '$50' | '$200' | '$1000';

export const STAKES: ReadonlyArray<StakeLabel> = ['$2', '$10', '$50', '$200', '$1000'];

export interface CashTableState {
  table_id: string;
  stake_label: StakeLabel;
  big_blind: number;
  min_buy_in: number;
  max_buy_in: number;
  seat_count: number;
  seats: (string | null)[];
  stacks: Record<string, number>;
  hand_in_progress: boolean;
}

export interface PlayerBankrollState {
  player_id: string;
  chips: number;
  starting_bankroll: number;
}

/** The session-summary half of /api/cash/state's response — set when
 *  the user has an active cash game to redirect to. `null` otherwise. */
export interface CashSessionState {
  game_id: string;
  stake_label: string | null;
}

/** Top-level /api/cash/state response. `bankroll` is always present
 *  so the stake picker can render affordability + locked tiers even
 *  when no session is active. */
export interface CashStateResponse {
  state: CashSessionState | null;
  bankroll: number;
}

export type HandStatus =
  | 'continue'
  | 'awaiting_human'
  | 'not_enough_players'
  | 'error';

export interface HandResult {
  status: HandStatus;
  hand_number: number;
  bust_seats: string[];
  error: string | null;
  awaiting_player_name: string | null;
}

export interface CashApiResponse {
  state: CashSessionState;
  result?: HandResult;
  session_ended?: boolean;
}

export type CashAction = 'fold' | 'check' | 'call' | 'raise' | 'all_in';

/**
 * One concrete sponsor offer materialized for a specific stake table.
 *
 * Two flavors share the shape (handoff §B.6 / §B.7 "mixed pool"):
 *   - `kind: 'house'`: anonymous archetype loan (v1 sponsorship).
 *     Carries `archetype_id`; lender fields are absent.
 *   - `kind: 'personality'`: AI-personality lender (Path B). Carries
 *     `lender_id`, `name` from the personality, optional
 *     `relationship_hint` surfaced from the lender's view of the
 *     player ("trusts you", "wants their money back", ...).
 *
 * The server recomputes `amount`/`floor`/`rate` from authoritative
 * state when the client commits the offer — clients can't tamper with
 * terms.
 */
export type SponsorOffer =
  | {
      kind: 'house';
      archetype_id: string;
      name: string;
      amount: number;
      floor: number;
      rate: number;
      flavor: string;
    }
  | {
      kind: 'personality';
      lender_id: string;
      name: string;
      amount: number;
      floor: number;
      rate: number;
      flavor: string;
      relationship_hint: string;
    };

/** Response from GET /api/cash/sponsor-offers. */
export type SponsorOffersResponse =
  | { eligible: true; stake_label: StakeLabel; offers: SponsorOffer[] }
  | { eligible: false; reason: string; bankroll: number; this_min_buy_in: number };

/** Payload of the `cash_bust` / `cash_rebuy_needed` SocketIO events. */
export interface CashBustEvent {
  game_id: string;
  stake_label: StakeLabel;
  min_buy_in: number;
  max_buy_in: number;
  bankroll: number;
  has_active_loan: boolean;
}

// --- Lobby v1.5 ---

export type AffordabilityState = 'affordable' | 'sponsor_eligible' | 'locked';

export type LobbySeat =
  | { kind: 'open'; index: number }
  | {
      kind: 'ai';
      index: number;
      personality_id: string;
      name: string;
      avatar_url: string | null;
      /** Current emotion driving the avatar image. For AIs at the
       *  player's active table this reflects live psychology;
       *  otherwise defaults to 'confident' (a priority emotion that's
       *  always pre-generated). Full Path C will source live emotion
       *  from background sim for unseated tables too. */
      emotion: string;
      chips: number;
      relationship_hint: string;
      /** Phase 3 Commit 1: present when the player has an outstanding
       *  carry to this AI. Aggregates across any past sessions that
       *  produced a residual debt to the same lender. Absent when
       *  there's no carry — frontend uses presence (not >0) to decide
       *  whether to render the corner pin. */
      carry_amount?: number;
    }
  | {
      kind: 'human';
      index: number;
      personality_id: string;
      chips: number;
    };

export interface LobbyTable {
  table_id: string;
  stake_label: StakeLabel;
  big_blind: number;
  min_buy_in: number;
  max_buy_in: number;
  affordability: AffordabilityState;
  seats: LobbySeat[];
  /** Seat index of the dealer button on this table. Rotates clockwise
   *  with each simulated hand in the lobby. `null` when the table has
   *  no occupied seats. Server-side state is in-memory only — purely
   *  cosmetic, expect it to reseed after a backend restart. */
  dealer_index?: number | null;
}

/** One lobby movement event surfaced to the activity ticker.
 *  Sourced from `cash_mode/activity.py` in-memory ring buffer; reset
 *  on backend restart. `message` is the display string. */
export interface LobbyEvent {
  type: 'join' | 'leave' | 'big_win' | 'big_loss';
  table_id: string;
  stake_label: string;
  personality_id: string;
  name: string;
  /** Semantics vary by event type:
   *   - join: empty
   *   - leave: movement decision name (`forced_leave`,
   *     `stake_up_queued`, `take_break`, `bored_move`)
   *   - big_win / big_loss: the opponent's personality_id (so the
   *     frontend can group win+loss pairs or filter per AI) */
  reason: string;
  message: string;
  created_at: string;
}

export interface LobbyResponse {
  bankroll: number;
  tables: LobbyTable[];
  events: LobbyEvent[];
}

/** Successful response from POST /api/cash/sit. */
export interface SitResponse {
  game_id: string;
  table_id: string;
  seat_index: number;
}

/** 402 body when the player tapped a sponsor-required seat. */
export interface SitRequiresSponsor {
  requires_sponsor: true;
  stake_label: StakeLabel;
  bankroll: number;
  min_buy_in: number;
  max_buy_in: number;
}

// --- Net Worth (Phase 3 Commit 1) ---

/** Carry-load-driven gate on offer quality. Mirrors `staking_tier.py`
 *  string constants — keep in lockstep when adding tiers. */
export type TierStatus = 'premium' | 'standard' | 'restricted' | 'house_only';

/** One outstanding carry the player owes to a staker. The "owed" number
 *  is `carry_amount` — the unrecovered portion of principal after the
 *  staker recovered what they could at the borrower's leave-time bust.
 *  `principal` is shown as context (the original stake size). */
export interface Payable {
  stake_id: string;
  staker_id: string;
  staker_kind: 'personality' | 'human';
  staker_display_name: string;
  carry_amount: number;
  principal: number;
  stake_tier: StakeLabel;
  /** ISO 8601 timestamp of stake creation. */
  created_at: string | null;
}

/** Response from GET /api/cash/net-worth. */
export interface NetWorthResponse {
  bankroll: number;
  /** The stake label whose tier_status / carry_cap applies. */
  tier_stake_label: StakeLabel;
  tier_status: TierStatus;
  /** 10 × min_buy_in @ tier_stake_label. */
  carry_cap: number;
  payables: Payable[];
  /** Phase 5 stub — empty list for v1. Layout slot reserved. */
  receivables: never[];
  /** bankroll + Σreceivables − Σpayables */
  net_worth: number;
  /** max(0, carry_cap − Σpayables) — remaining carry headroom before
   *  tier degrades. */
  available: number;
}

/** Response from POST /api/cash/stakes/<id>/payoff. */
export interface PayoffResponse {
  stake_id: string;
  status: 'settled';
  paid: number;
  bankroll: number;
  staker_id: string;
}

/** Response from POST /api/cash/stakes/<id>/request-forgiveness. */
export interface ForgivenessResponse {
  stake_id: string;
  granted: boolean;
  /** `'settled'` when granted; `'carry'` when refused (stake unchanged). */
  status: 'settled' | 'carry';
  staker_id: string;
  staker_display_name: string;
  /** The weighted relationship-axes score the decision used. */
  score: number;
  /** The threshold the score had to exceed for forgiveness. */
  threshold: number;
}

/** 429 body when an ask is inside the 24h rate-limit window. */
export interface ForgivenessRateLimited {
  error: string;
  retry_after_seconds: number;
}
