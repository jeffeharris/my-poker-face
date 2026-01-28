import type { Player } from './player';
import type { ChatMessage } from './chat';

/**
 * Betting context from the backend.
 * Provides all betting constraints using "raise TO" semantics.
 */
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
}

/** Player's showdown hand information */
export interface PlayerShowdownInfo {
  cards: string[] | { rank: string; suit: string }[];
  hand_name: string;
  hand_rank: number;
  kickers?: string[];
}

/** Pot breakdown for split/side pots */
export interface PotBreakdown {
  pot_name: string;
  total_amount: number;
  winners: { name: string; amount: number }[];
  hand_name?: string;
}

// Used by WinnerAnnouncement component
export interface WinnerInfo {
  winners: string[];
  winnings?: { [key: string]: number };  // Optional - may use pot_breakdown instead
  pot_breakdown?: PotBreakdown[];  // New format from backend
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