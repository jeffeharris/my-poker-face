import type { Player } from './player';
import type { ChatMessage } from './chat';

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
  messages: ChatMessage[];
}

// Used by WinnerAnnouncement component
export interface WinnerInfo {
  winners: string[];
  winnings: { [key: string]: number };
  hand_name: string;
  winning_hand?: string[];
  showdown: boolean;
  players_cards?: { [key: string]: string[] };
  community_cards?: any[];
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