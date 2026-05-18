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

export interface CashSessionState {
  table: CashTableState;
  player_bankroll: PlayerBankrollState;
  hand_number: number;
  player_disconnected: boolean;
  player_pending_quit: boolean;
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
