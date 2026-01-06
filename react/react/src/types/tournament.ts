/**
 * Tournament types for end-game handling and stats
 */

// Backend card format (from Python API)
export interface BackendCard {
  rank: string;
  suit: string;
}

export interface PlayerStanding {
  player_name: string;
  is_human: boolean;
  finishing_position: number;
  eliminated_by: string | null;
  eliminated_at_hand: number | null;
}

export interface TournamentResult {
  winner: string;
  standings: PlayerStanding[];
  total_hands: number;
  biggest_pot: number;
  human_position: number | null;
  game_id: string;
  human_eliminated?: boolean;
  final_hand_data?: {
    winners: string[];
    winnings: { [key: string]: number };
    hand_name: string;
    showdown: boolean;
    players_showdown?: { [key: string]: {
      cards: BackendCard[];
      hand_name: string;
      hand_rank: number;
      kickers?: string[];
    }};
    community_cards?: BackendCard[];
    is_final_hand?: boolean;
    tournament_outcome?: {
      human_won: boolean;
      human_position: number;
    };
  };
}

export interface EliminationEvent {
  eliminated: string;
  eliminator: string | null;
  finishing_position: number;
  hand_number: number;
  remaining_players: number;
}

export interface CareerStats {
  player_name: string;
  games_played: number;
  games_won: number;
  total_eliminations: number;
  best_finish: number | null;
  worst_finish: number | null;
  avg_finish: number | null;
  biggest_pot_ever: number;
  win_rate: number;
}

export interface TournamentHistoryEntry {
  game_id: string;
  winner_name: string;
  total_hands: number;
  biggest_pot: number;
  player_count: number;
  your_position: number;
  eliminated_by: string | null;
  ended_at: string;
}

export interface EliminatedPersonality {
  name: string;
  first_eliminated_at: string;
  times_eliminated: number;
}

/**
 * Helper to get ordinal suffix for a number (1st, 2nd, 3rd, etc.)
 */
export function getOrdinal(n: number): string {
  const s = ['th', 'st', 'nd', 'rd'];
  const v = n % 100;
  return n + (s[(v - 20) % 10] || s[v] || s[0]);
}
