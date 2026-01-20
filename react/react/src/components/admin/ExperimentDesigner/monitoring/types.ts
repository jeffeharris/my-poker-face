/**
 * TypeScript interfaces for the Live Monitoring View
 */

/**
 * Card representation for monitoring view
 */
export interface MonitoringCard {
  rank: string;
  suit: string;
}

/**
 * Psychology data for a player
 */
export interface PlayerPsychology {
  narrative: string;
  inner_voice: string;
  tilt_level: number;
  tilt_category: string;
  tilt_source: string;
}

/**
 * LLM debug info for a player
 */
export interface PlayerLLMDebug {
  provider?: string;
  model?: string;
  reasoning_effort?: string;
  total_calls?: number;
  avg_latency_ms?: number;
  avg_cost_per_call?: number;
  p95_latency_ms?: number;
  p99_latency_ms?: number;
}

/**
 * Player data in a live game snapshot
 */
export interface MonitoringPlayer {
  name: string;
  stack: number;
  bet: number;
  hole_cards: MonitoringCard[];
  is_folded: boolean;
  is_all_in: boolean;
  is_current: boolean;
  psychology: PlayerPsychology;
  llm_debug: PlayerLLMDebug;
}

/**
 * A single game snapshot for monitoring
 */
export interface GameSnapshot {
  game_id: string;
  variant: string | null;
  phase: string;
  hand_number: number;
  pot: number;
  community_cards: MonitoringCard[];
  players: MonitoringPlayer[];
}

/**
 * Response from the live-games endpoint
 */
export interface LiveGamesResponse {
  success: boolean;
  games: GameSnapshot[];
  experiment_status: string;
  error?: string;
}

/**
 * Play style analysis from opponent models
 */
export interface PlayStyle {
  vpip: number;
  pfr: number;
  aggression_factor: number;
  hands_observed: number;
  summary: string;
}

/**
 * A single decision record
 */
export interface RecentDecision {
  hand_number: number;
  phase: string;
  action: string;
  decision_quality: string;
  ev_lost: number | null;
}

/**
 * Detailed player info for drill-down panel
 */
export interface PlayerDetail {
  player: {
    name: string;
    stack: number;
    cards: MonitoringCard[];
  };
  psychology: PlayerPsychology;
  psychology_enabled: boolean;
  llm_debug: PlayerLLMDebug;
  play_style: PlayStyle;
  recent_decisions: RecentDecision[];
}

/**
 * Response from the player detail endpoint
 */
export interface PlayerDetailResponse extends PlayerDetail {
  success: boolean;
  error?: string;
}

/**
 * Selected player state for drill-down
 */
export interface SelectedPlayer {
  gameId: string;
  playerName: string;
}
