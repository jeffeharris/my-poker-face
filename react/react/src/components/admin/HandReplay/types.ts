/**
 * TypeScript interfaces for the Hand Replay feature
 */

/** A player in a recorded hand (matches API response from PlayerHandInfo.to_dict()).
 *  Note: seat_index and hole_cards are enriched client-side by HandReplayBrowser,
 *  not returned directly by the API. */
export interface HandPlayer {
  name: string;
  seat_index: number;
  starting_stack: number;
  hole_cards: string[] | null;
  position: string;
  is_human: boolean;
}

/** A single action within a hand (matches API enriched action) */
export interface HandAction {
  index: number;
  phase: string;
  player_name: string;
  action: string;
  amount: number;
  pot_after: number;
  community_cards_visible: string[];
  position: string;
  is_human: boolean;
}

/** Winner info for the hand (matches API response from WinnerInfo.to_dict()) */
export interface WinnerInfo {
  name: string;
  amount_won: number;
  hand_name: string | null;
  hand_rank: number | null;
}

/** Enrichment data for a single action (optional) */
export interface EnrichmentData {
  equity: number | null;
  decision_quality: string | null;
  ai_thinking: string | null;
}

/** Full replay data returned by the API */
export interface HandReplayData {
  game_id: string;
  hand_number: number;
  summary: string;
  players: HandPlayer[];
  actions: HandAction[];
  winners: WinnerInfo[];
  hole_cards: Record<string, string[]>;
  community_cards: string[];
  community_cards_by_phase: Record<string, string[]>;
  pot_size: number;
  was_showdown: boolean;
  deck_seed: number | null;
  enrichment?: Record<number, EnrichmentData> | null;
}

/** Hand list item returned by the hand list API */
export interface HandListItem {
  hand_number: number;
  timestamp: string;
  player_count: number;
  pot_size: number;
  was_showdown: boolean;
  winner_names: string[];
  action_count: number;
  summary: string;
}

/** Computed visual state for the table at a given action index */
export interface VisualPlayer {
  name: string;
  seat_index: number;
  stack: number;
  bet: number;
  hole_cards: string[] | null;
  position: string;
  is_folded: boolean;
  is_all_in: boolean;
  is_current: boolean;
  last_action: string | null;
}

export interface VisualState {
  players: VisualPlayer[];
  community_cards: string[];
  pot: number;
  phase: string;
  current_player_name: string | null;
}

/** Phase ordering for comparisons */
export const PHASE_ORDER: Record<string, number> = {
  PRE_FLOP: 0,
  FLOP: 1,
  TURN: 2,
  RIVER: 3,
  SHOWDOWN: 4,
};
