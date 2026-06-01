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

export type HandStatus = 'continue' | 'awaiting_human' | 'not_enough_players' | 'error';

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
  | {
      eligible: true;
      stake_label: StakeLabel;
      offers: SponsorOffer[];
      /** Player-prestige hook 2: true when the player is too reviled
       *  (room-level regard too low) for any named-AI backing — only house
       *  offers show. The modal explains why. Absent ⇒ false. */
      backing_restricted?: boolean;
    }
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
      /** Phase 4 UI extra: true when this AI is currently in any
       *  active stake position (borrower OR staker side). Drives a
       *  small "in stake" glyph on the table card so the player can
       *  see live AI-economy dynamics without opening each dossier. */
      in_active_stake?: boolean;
      /** Scouting tag for the lobby card. `fish` = the loose/passive
       *  donor archetype; `whale` = a fish whose persistent bankroll is
       *  deep relative to the stakes (a prime target). Absent for
       *  non-fish AIs. Fish are casino-only, so this mostly appears on
       *  the Casino tab. Computed server-side (see WHALE_BANKROLL_MULTIPLE). */
      role?: 'fish' | 'whale';
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
  /** v111: user-facing label ("The Lodge"). `null` when no name has
   *  been set — UI falls back to the stake label. Hard-coded in
   *  `cash_mode/lobby_config.py` for lobby tables; private/casino
   *  tables (future) will source their own. */
  table_name?: string | null;
  /** v111: table-type discriminator. `'lobby'` = the Cardroom career
   *  ladder; `'casino'` = the ephemeral $2 fish floor (own tab); `'scripted'` =
   *  a pinned tutorial table (Scene 0). `'private'` stays reserved for a future
   *  invite-only feature. */
  table_type?: 'lobby' | 'private' | 'casino' | 'scripted';
  /** v113: hands left before this (casino) table tears down. Present →
   *  the table is in its closing countdown; `null`/absent → active. Drives
   *  the "closing" tag on the card and the Casino tab's closing indicator. */
  closing_hand_countdown?: number | null;
}

/** One lobby movement event surfaced to the activity ticker.
 *  Sourced from `cash_mode/activity.py` in-memory ring buffer; reset
 *  on backend restart. `message` is the display string. */
export interface LobbyEvent {
  type:
    | 'join'
    | 'leave'
    | 'big_win'
    | 'big_loss'
    | 'all_in'
    | 'bust'
    | 'burst_summary'
    // Phase 4 of the backing system.
    | 'ai_stake'
    | 'ai_default'
    // Phase 4.5 — AI-initiated carry resolution.
    | 'ai_payoff'
    | 'ai_forgiven'
    // Vice spending — AI goes off-grid for a duration.
    | 'vice_start'
    | 'vice_end'
    // Side hustle — broke AI goes off-grid to earn (mirror of vice).
    | 'hustle_start'
    | 'hustle_end'
    // Last stand — an AI (or the player) has their entire bankroll on a
    // single table. The predator signal: a vulnerable seat to target.
    | 'last_stand'
    // Whales — a rare pool-funded high roller at a cardroom table. Arrival
    // is the pull signal; departure is the quiet provisioning recall.
    | 'whale_arrival'
    | 'whale_departure'
    // AI asking the human staker to forgive an outstanding carry.
    | 'ai_requests_forgiveness'
    // v121 — the human's reputation quadrant changed (read-only scoreboard
    // beat; `reason` carries the new quadrant label).
    | 'reputation_shift'
    // v124 — an AI vouched the player into a new cardroom (Act-1 career spine).
    // A door opens: the revealed room is `table_id`/`stake_label`, the voucher
    // is `personality_id`/`name`, `message` is the pre-formatted line.
    | 'vouch';
  table_id: string;
  stake_label: string;
  personality_id: string;
  name: string;
  /** Semantics vary by event type:
   *   - join: empty
   *   - leave: movement decision name (`forced_leave`,
   *     `stake_up_queued`, `take_break`, `bored_move`)
   *   - big_win / big_loss: the opponent's personality_id (so the
   *     frontend can group win+loss pairs or filter per AI)
   *   - ai_stake: the borrower's personality_id (counterparty)
   *   - ai_default: the staker's personality_id (counterparty —
   *     applies to both Phase 4 natural-carry and Phase 4.5 explicit
   *     defaults; the `message` verb distinguishes the two)
   *   - ai_payoff: the staker's personality_id (counterparty)
   *   - ai_forgiven: the borrower's personality_id (the staker is
   *     the actor in a forgive grant, so they lead `personality_id`)
   *   - last_stand: empty for AIs; `'self'` for the player's own line */
  reason: string;
  message: string;
  created_at: string;
  /** Groups every event from one sim hand. Set only on the single-hand
   *  path; null for non-hand and burst-compressed events. */
  hand_id?: string | null;
  /** Whether the ticker renders this row. The single-hand path emits one
   *  composed `primary` summary per hand and demotes its atomic
   *  win/all-in/bust events to `primary: false` — kept on the wire for
   *  per-AI filtering, hidden from the feed. Absent ⇒ treated as primary. */
  primary?: boolean;
}

/** An AI currently on a vice — off-grid for a bounded duration. */
export interface ActiveVice {
  personality_id: string;
  /** Display name from the personality config. */
  name: string;
  /** The LLM-generated (or templated fallback) flavor line. */
  narration: string;
  /** 'short' | 'medium' | 'long'. */
  duration_bucket: string;
  /** ISO-8601 UTC. */
  started_at: string;
  /** ISO-8601 UTC — when the AI re-enters the eligibility pool. */
  ends_at: string;
  /** Chips spent on this vice. */
  amount: number;
}

/** One vertex of the career-hero net-worth sparkline. `value` is net
 *  worth in chips; `t` is the ISO-8601 UTC timestamp it was reached. */
export interface BankrollPoint {
  t: string;
  value: number;
}

/** The four reputation quadrants — kept in lockstep with the QUADRANT_*
 *  constants in cash_mode/prestige.py. */
export type ReputationQuadrant =
  | 'Beloved Legend'
  | 'Infamous Villain'
  | 'Up-and-comer'
  | 'Disliked Nobody';

/** Per-axis component breakdown (already-weighted contributions) — surfaced
 *  for the explain/debug affordance on the panel. renown_* sum to ~renown
 *  (pre-ratchet); regard_* sum to ~regard (pre-clamp). */
export interface ReputationComponents {
  breadth: number;
  tenure: number;
  stake_tier: number;
  beat_respected: number;
  high_stakes: number;
  likability: number;
  respect: number;
  heat: number;
}

/** The human player's reputation scoreboard. Absent (null) until the world
 *  ticker has captured at least once (~minutes into a new sandbox). */
export interface ReputationData {
  /** [0, 1] — fame magnitude; ratchets up, behaviour-agnostic. */
  renown: number;
  /** [-1, 1] — how the room feels; swings, partially decays with heat. */
  regard: number;
  quadrant: ReputationQuadrant;
  /** How many AIs have an opinion of you (inbound relationship edges). */
  opponent_count: number;
  /** ISO-8601 UTC of the capture. */
  computed_at: string;
  components: ReputationComponents;
}

export interface LobbyResponse {
  bankroll: number;
  tables: LobbyTable[];
  /** The table the player is currently seated at (live session), or
   *  null when they have no active game. Drives the "you're seated here"
   *  pin + Resume action on the matching TableCard. Can be null even when
   *  a session is active — see `has_active_session`. */
  seated_table_id?: string | null;
  /** Stake label of the active session's table, for the Resume bar text.
   *  Populated from the durable cash_sessions row so it survives a cold
   *  (DB-only) session whose table isn't in the rendered lobby list. */
  seated_stake_label?: string | null;
  /** DB-aware truth: the player has a cash-* game row (live OR cold). This
   *  drives the Resume bar so an abandoned mid-hand session that isn't in
   *  memory (null `seated_table_id`) still has a one-tap path back in/out.
   *  Without it the player is wedged — the backend 409s every new sit but
   *  the lobby shows no active session. */
  has_active_session?: boolean;
  /** ISO start time of the active session, for the Resume bar's
   *  "Paused Xh ago" hint. From the durable cash_sessions row, so it
   *  works for cold (DB-only) sessions too. Null when no active session. */
  seated_since?: string | null;
  events: LobbyEvent[];
  /** v110 — count of AI-borrower carries asking the player to forgive.
   *  Drives the wallet badge in the Lobby header. The full request
   *  list is fetched via GET /api/cash/forgiveness-requests when the
   *  Net Worth Drawer opens. */
  pending_forgiveness_count?: number;
  /** AIs currently off-grid on a vice. Per-sandbox. Frontend renders
   *  these in a separate "Away" group (or simply suppresses the
   *  personality from lobby cards while the vice is active). The
   *  narration shows on the personality dossier + the vice_start
   *  ticker event. */
  active_vices?: ActiveVice[];
  /** How fast the realtime background ticker advances this user's world.
   *  Set via PUT /api/cash/world-pace; drives the lobby pace selector. */
  world_pace?: WorldPace;
  /** Net-worth trajectory (oldest → newest) for the career hero's
   *  sparkline: `{t, value}` change-points read from `holdings_snapshots`,
   *  where `value` is net worth (chips + receivable − outstanding) and `t`
   *  is the ISO timestamp it was first reached. Consecutive-equal idle
   *  samples are collapsed, so the curve reads as the sequence of changes
   *  and each vertex has a real time to show on hover. Empty until the
   *  world ticker has recorded ≥1 point. */
  bankroll_history?: BankrollPoint[];
  /** Net result of the player's most recent finished session
   *  (`player_take_home − total_buy_in`). Signed; null until the first
   *  session is finalised. Drives the hero's up/down delta chip. */
  last_session_delta?: number | null;
  /** v121 — the player's reputation scoreboard (renown + regard + quadrant).
   *  Null until the world ticker's first prestige capture; the panel renders
   *  nothing while absent. Read-only — no AI behaviour reads it (yet). */
  reputation?: ReputationData | null;
  /** v124 — true for a brand-new career player who hasn't been through the
   *  Lucky Stack intake (cold open) yet; the frontend shows the intake beat
   *  before the lobby. `fish_name` is the tourist handle once christened. */
  intake_needed?: boolean;
  fish_name?: string | null;
  /** One-shot, right after Scene-0 graduation: Sal escorts the player to the
   *  revealed home court. The lobby shows his portrait + bubble (`line`) and
   *  spotlights the `table_id` card. Served once then cleared server-side, so
   *  it's present on exactly one load. Null when there's no handoff pending. */
  mentor_intro?: MentorIntro | null;
  /** Sal's STANDING mentor-stake offer (the comp-return's other half): after
   *  graduation, broke at 0, Sal backs the player's first real seat at their
   *  home court. Unlike the one-shot `mentor_intro` walk-over, this persists in
   *  the lobby until taken. When present, the frontend sits the home-court seat
   *  via `sponsor-and-sit(lender_id)` instead of the generic SponsorModal. Null
   *  once spent (or when not graduated / not broke / no home court). */
  mentor_stake?: MentorStake | null;
}

export interface MentorIntro {
  table_id: string;
  name: string;
  line: string;
}

export interface MentorStake {
  table_id: string;
  lender_id: string;
  lender_name: string;
  stake_label: StakeLabel;
}

/** How fast the background world ticks for unseated tables. */
export type WorldPace = 'subtle' | 'lively' | 'bustling';

/** Where an AI is right now, from the whereabouts view.
 *  `unknown` is a degenerate state (referenced but untrackable). */
export type WhereaboutsStatus = 'seated' | 'idle' | 'side_hustle' | 'vice' | 'unknown';

/** Idle-pool reason — why an AI stepped away from the tables.
 *  Mirrors `cash_mode/tables.py::IDLE_REASONS`. */
export type IdleReason = 'forced_leave' | 'stake_up_queued' | 'take_break' | 'bored_move';

/** One AI's location + state for the whereabouts surfaces. The player
 *  drawer reads the `met`-filtered subset (with `emotion`/`avatar_url`);
 *  the admin panel reads the full set (with `stuck`/`sandbox_id`). */
export interface WhereaboutsPerson {
  personality_id: string;
  name: string;
  status: WhereaboutsStatus;
  /** Has the human tangled with them in cash (chips flowed)? */
  met: boolean;
  hands_played: number;
  /** Player's lifetime PnL vs them — positive = you're up on them. */
  net_pnl: number;
  bankroll: number | null;
  // --- location (status === 'seated') ---
  table_id: string | null;
  table_name: string | null;
  stake_label: string | null;
  seat_index: number | null;
  /** >1 only on the double-seat bug; null otherwise. */
  seat_count: number | null;
  chips_on_table: number;
  // --- off-grid detail (idle / side_hustle / vice) ---
  reason: IdleReason | null;
  target_stake: string | null;
  narration: string | null;
  amount: number | null;
  started_at: string | null;
  ends_at: string | null;
  left_at: string | null;
  /** Seconds since entering the current state. */
  seconds_in_state: number | null;
  /** Seconds until hustle/vice ends; negative = overdue. */
  seconds_remaining: number | null;
  /** Recharge fraction 0..1 toward the AI's baseline while resting in the idle
   *  pool (1.0 = fully rested); null for seated/off-grid AIs. */
  recharge: number | null;
  /** The AI's last few notable hand events (bust/suckout/big pot), newest
   *  last — the world's short-term memory. [] when no recent drama. */
  recent: { type: string; amount: number; opponent: string | null }[];
  // --- player route enrichment ---
  emotion?: string;
  avatar_url?: string | null;
  // --- admin route only ---
  /** Hard invariant-violation flags (real bugs); empty/absent = healthy. */
  stuck?: string[];
  /** Soft/temporal flags (overdue/stale) — expected after the player's
   *  been away; informational, not alarms. */
  watch?: string[];
  sandbox_id?: string;
  sandbox_owner_id?: string;
}

/** GET /api/cash/whereabouts — met-filtered player view. */
export interface WhereaboutsResponse {
  now: string;
  people: WhereaboutsPerson[];
  /** Count of trackable AIs around that the player hasn't met yet —
   *  a teaser only, no identities (fog of war). */
  unmet_count: number;
  counts: {
    total: number;
    idle: number;
    side_hustle: number;
    vice: number;
    seated: number;
  };
}

/** Socket `lobby_tick` payload — a lightweight nudge telling a mounted
 *  lobby to refetch its snapshot. The world itself advances server-side
 *  in the ticker; this just says "something may have changed." */
export interface LobbyTick {
  sandbox_id: string;
  ts: number;
}

/** Socket `world_event` payload. Same wire shape as a lobby event, but
 *  pushed in realtime so a toast/feed can surface it without a refetch. */
export type WorldEvent = LobbyEvent;

/** Successful response from POST /api/cash/sit. */
export interface SitResponse {
  game_id: string;
  table_id: string;
  seat_index: number;
}

/** 402 body when the player tapped a sponsor-required seat. The
 *  backend echoes the table_id + the seat it actually reserved — which
 *  may differ from the tapped seat if live-fill took it and the server
 *  fell back to another open seat — so the SponsorModal must target
 *  these rather than the originally-tapped index. */
export interface SitRequiresSponsor {
  requires_sponsor: true;
  stake_label: StakeLabel;
  bankroll: number;
  min_buy_in: number;
  max_buy_in: number;
  table_id: string;
  seat_index: number;
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

/** Phase 5 — one stake the player has skin in, from their POV as staker.
 *
 *  Two flavors share this shape, distinguished by `status`:
 *    - `'active'`: stake is in flight — `amount` is principal+match
 *      currently sitting on the borrower's table seat. Settles at the
 *      AI's leave time (could pay full, could carry, could win
 *      upside). Surfaced so the player can see "what's in play."
 *    - `'carry'`: stake settled but the AI busted under-water — `amount`
 *      is the residual debt (`carry_amount`). Same semantic as Payable
 *      from the other side.
 *
 *  `amount` is the unified value the UI renders; the row's framing
 *  ("in play" vs "owed") comes from `status`. `principal`, `match_amount`,
 *  `cut`, and `format` are exposed for richer breakdowns / future UI. */
export interface Receivable {
  stake_id: string;
  borrower_id: string;
  borrower_kind: 'personality' | 'human';
  borrower_display_name: string;
  /** Unified display amount: principal+match for active, carry_amount
   *  for carry. The status field tells the UI which framing to use. */
  amount: number;
  carry_amount: number;
  principal: number;
  match_amount: number;
  stake_tier: StakeLabel;
  status: 'active' | 'carry';
  format: 'pure' | 'match_share' | 'house';
  cut: number;
  created_at: string | null;
}

/** One closed-out stake the player touched — either as staker or
 *  borrower. Surfaces a history trail so cleanly-settled stakes
 *  (chips just returned to bankroll) and explicit defaults (no chip
 *  movement at all) leave a visible record. */
export interface StakeHistoryRow {
  stake_id: string;
  /** Was the player the staker or borrower on this stake? Drives
   *  the "from / to" framing in the row. */
  role: 'staker' | 'borrower';
  status: 'settled' | 'defaulted';
  /** The other side's id (null for house stakes). */
  counterparty_id: string | null;
  counterparty_kind: 'personality' | 'human' | 'house';
  counterparty_display_name: string;
  principal: number;
  match_amount: number;
  stake_tier: StakeLabel;
  format: 'pure' | 'match_share' | 'house';
  cut: number;
  /** Settlement chip flows captured at settle time (v106+). NULL on
   *  legacy rows that settled before the schema upgrade — UI hides
   *  the P&L line in that case. */
  staker_payout: number | null;
  borrower_payout: number | null;
  /** Net change in the player's chips from this single stake.
   *  Positive = won, negative = lost. NULL when payouts weren't
   *  captured. */
  net_for_player: number | null;
  created_at: string | null;
  settled_at: string | null;
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
  /** Phase 5 — active stakes + carries (both directions) the player
   *  has open. Active rows surface as "in play"; carries as "owed". */
  receivables: Receivable[];
  /** Phase 5 — recent closed-out stakes (settled / defaulted). Most
   *  recent first, capped at 20. */
  history: StakeHistoryRow[];
  /** bankroll + Σreceivables − Σpayables */
  net_worth: number;
  /** max(0, carry_cap − Σpayables) — remaining carry headroom before
   *  tier degrades. */
  available: number;
  /** v110 — count of AI-borrower carries asking the player to forgive.
   *  Drives the wallet badge in the Lobby header; the actual request
   *  list is fetched via GET /api/cash/forgiveness-requests on demand. */
  pending_forgiveness_count: number;
}

// --- v110: AI-to-player forgiveness consent flow ---

export interface ForgivenessRequest {
  stake_id: string;
  borrower_id: string;
  borrower_display_name: string;
  carry_amount: number;
  stake_tier: StakeLabel;
  /** ISO timestamp of when the AI surfaced the ask. */
  pending_since: string | null;
  /** ISO timestamp of when the original stake was opened. */
  created_at: string | null;
}

export interface ForgivenessRequestsResponse {
  requests: ForgivenessRequest[];
}

export interface StakerForgiveResponse {
  stake_id: string;
  granted: boolean;
  /** 'settled' on grant, 'carry' on refuse. */
  status: 'settled' | 'carry';
  borrower_id: string;
  borrower_display_name: string;
}

// --- Phase 5: Player as staker ---

export type StakeFormat = 'pure' | 'match_share';

/** Body of POST /api/cash/stakes/offer. */
export interface StakeOfferRequest {
  target_pid: string;
  stake_label: StakeLabel;
  principal: number;
  cut: number;
  format?: StakeFormat;
  match_amount?: number;
  origination_fee?: number;
}

/** AI willingness-evaluation breakdown — returned on accept and on
 *  most refuses so the player can see why an offer landed where it
 *  did. The base/penalty/relief decomposition exposes the math
 *  rather than hiding it, so the player learns to read AI mood. */
export interface OfferEvaluation {
  score: number;
  base_threshold: number;
  cut_penalty: number;
  desperation: number;
  desperation_relief: number;
  effective_threshold: number;
}

/** 200 body when the AI accepted the offer. */
export interface StakeOfferAccepted {
  accepted: true;
  stake_id: string;
  target_pid: string;
  target_display_name: string;
  principal: number;
  match_amount: number;
  origination_fee: number;
  format: StakeFormat;
  cut: number;
  stake_label: StakeLabel;
  table_id: string;
  seat_index: number;
  evaluation: OfferEvaluation;
  bankroll: number;
}

/** 200 body when the AI decided against the offer. Distinct from
 *  client-error 4xx rejections (bankroll gate, invalid input). */
export interface StakeOfferRefused {
  accepted: false;
  reason:
    | 'unwilling'
    | 'tier_blocked'
    | 'cooldown'
    | 'no_history'
    | 'heat'
    | 'dislike'
    | 'ai_underfunded'
    | 'cut_too_steep'
    | 'low_goodwill'
    // legacy reason kept for back-compat during rollout
    | 'relationship';
  target_pid: string;
  target_display_name: string;
  detail: string;
  /** Present when the AI got far enough to run the willingness math. */
  evaluation?: OfferEvaluation;
  score?: number;
  threshold?: number;
}

export type StakeOfferResponse = StakeOfferAccepted | StakeOfferRefused;

/** One AI eligible to receive a player-offered stake right now.
 *  `target_stake_label` (parent tier) determines the only tier the
 *  player can stake them at (comfort_zone +1, per the "help them
 *  work up the ranks" rule). */
export interface StakableAiCandidate {
  personality_id: string;
  name: string;
  comfort_zone: StakeLabel;
  suggested_principal: number;
  relationship_hint: string;
  likability: number;
  respect: number;
  heat: number;
  /** How desperate this AI is right now — higher means they'll
   *  tolerate worse terms. Surfaced so the UI can hint at easier
   *  sells without exposing the formula. */
  desperation: number;
  /** AI's personality `ego` anchor (0..1). Drives the willingness
   *  math alongside `desperation`. */
  ego: number;
  /** Portrait URL ("/api/avatar/<name>/<emotion>/full"), or null if no
   *  avatar exists yet — the card falls back to a name initial. */
  avatar_url: string | null;
  /** Current display emotion driving the portrait (persisted sim state,
   *  defaulting to "confident"). Also feeds the dossier wax-seal badge. */
  emotion: string;
}

/** One target-tier section of stakable candidates. */
export interface StakableAiTier {
  stake_label: StakeLabel;
  min_buy_in: number;
  max_buy_in: number;
  candidates: StakableAiCandidate[];
}

/** Response from GET /api/cash/stakable-ai. */
export interface StakableAiResponse {
  by_tier: StakableAiTier[];
  bankroll: number;
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

// --- File cabinet (dossier Phase 4) ---

/** One opponent in the file-cabinet roster — everyone you've accumulated
 *  scouting on, with the headline stats the UI sorts by. */
export interface FileCabinetPerson {
  personality_id: string;
  name: string;
  hands_observed: number;
  net_pnl: number; // observer POV: positive = you're up
  hands_played_cash: number;
  heat: number;
  respect: number;
  likability: number;
  last_seen: string | null;
  reads_unlocked: number;
  reads_total: number;
  floor_met: boolean;
  fully_unlocked: boolean;
}

export interface FileCabinetResponse {
  people: FileCabinetPerson[];
  people_met: number;
  dossiers_unlocked: number;
}
