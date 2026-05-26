import type { Player } from './player';
import type { ChatMessage } from './chat';

/**
 * Betting context from the backend.
 * Provides all betting constraints using "raise TO" semantics.
 */
export interface OpponentCover {
  name: string;
  nickname: string;
  stack: number;
  cover_amount: number; // Raise TO amount that puts this opponent all-in
}

export interface BettingContext {
  player_stack: number;
  player_current_bet: number;
  highest_bet: number;
  pot_total: number;
  min_raise_amount: number;
  available_actions: string[];
  // Computed properties
  cost_to_call: number;
  min_raise_to: number;
  max_raise_to: number;
  effective_stack: number;
  // Opponent cover amounts
  opponent_covers?: OpponentCover[];
}

export interface CashActiveLoan {
  /** Principal in chips. */
  amount: number;
  /** Repayment multiplier on principal (e.g., 1.30 = repay 130%). */
  floor: number;
  /** Sponsor's cut of the post-floor remainder (e.g., 0.40 = 40%). */
  rate: number;
  /** Personality id of the AI lender, or null for an anonymous house loan. */
  lender_id: string | null;
}

export interface CashModeInfo {
  stake_label: string;
  bankroll: number;
  big_blind: number;
  min_buy_in: number;
  max_buy_in: number;
  /** Present when the player has an outstanding sponsor loan; drives the
   *  leave-table settlement preview. Null when no loan is active. */
  active_loan?: CashActiveLoan | null;
  /** The specific table the player is seated at. `table_name` is the
   *  friendly room label ("The Lodge") shown in the in-game header chip
   *  + arrival toast. Both omitted for legacy sessions where the name
   *  wasn't resolved — the chip then simply doesn't render. */
  table_id?: string | null;
  table_name?: string | null;
}

export interface GameState {
  players: Player[];
  community_cards: string[];
  pot: { total: number };
  current_player_idx: number;
  current_dealer_idx: number;
  small_blind_idx: number;
  big_blind_idx: number;
  phase: string;
  highest_bet: number;
  player_options: string[];
  min_raise: number;
  big_blind: number;
  small_blind: number;
  hand_number: number;
  messages: ChatMessage[];
  betting_context?: BettingContext;
  newly_dealt_count?: number;
  awaiting_action?: boolean;
  run_it_out?: boolean;
  /** Present only for cash-mode games; surfaces bankroll + buy-in
   *  caps for the rebuy/topup UI. Tournament games omit this. */
  cash_mode?: CashModeInfo;
  /** True while AI decisions are resolving via the no-LLM tiered path.
   *  Set by POST /api/game/<id>/fast-forward, auto-cleared when action
   *  returns to the human. */
  fast_forward?: boolean;
}

/** Player's showdown hand information */
export interface PlayerShowdownInfo {
  cards: string[] | { rank: string; suit: string }[];
  hand_name: string;
  hand_rank: number;
  hand_score?: number;
  kickers?: string[];
}

/** Pot breakdown for split/side pots */
export interface PotBreakdown {
  pot_name: string;
  total_amount: number;
  winners: { name: string; amount: number }[];
  hand_name: string;
}

// Used by WinnerAnnouncement component
export interface WinnerInfo {
  winners: string[];
  winnings?: { [key: string]: number }; // Optional - may use pot_breakdown instead
  pot_breakdown?: PotBreakdown[]; // New format from backend
  pot_contributions?: { [key: string]: number }; // Player name -> amount contributed to pot
  hand_name: string;
  winning_hand?: string[];
  showdown: boolean;
  players_cards?: { [key: string]: string[] };
  players_showdown?: { [key: string]: PlayerShowdownInfo };
  community_cards?: string[] | { rank: string; suit: string }[];
  // Tournament final hand context
  is_final_hand?: boolean;
  tournament_outcome?: {
    human_won: boolean;
    human_position: number;
  };
}

// Alternative format used in some places
export interface WinnerInfoAlt {
  winners: Array<{
    name: string;
    hand_description: string;
    prize: number;
  }>;
  pot_total: number;
}
