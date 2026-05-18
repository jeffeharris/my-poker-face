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
