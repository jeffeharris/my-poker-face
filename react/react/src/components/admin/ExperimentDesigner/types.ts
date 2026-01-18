/**
 * TypeScript interfaces for the Experiment Designer
 */

export type ExperimentStatus = 'pending' | 'running' | 'completed' | 'failed' | 'paused';

export type ExperimentMode = 'design' | 'list' | 'detail';

export interface PromptConfig {
  pot_odds: boolean;
  hand_strength: boolean;
  session_memory: boolean;
  opponent_intel: boolean;
  strategic_reflection: boolean;
  chattiness: boolean;
  emotional_state: boolean;
  tilt_effects: boolean;
  mind_games: boolean;
  persona_response: boolean;
  memory_keep_exchanges: number;
}

export interface PlayerConfig {
  name: string;
  prompt_config?: Partial<PromptConfig>;
}

/**
 * Control (baseline) configuration for A/B testing experiments.
 */
export interface ControlConfig {
  label: string;
  model?: string;
  provider?: string;
  prompt_config?: Partial<PromptConfig>;
}

/**
 * Variant configuration that overrides control for A/B testing.
 */
export interface VariantConfig {
  label: string;
  model?: string;
  provider?: string;
  prompt_config?: Partial<PromptConfig>;
}

export interface ExperimentConfig {
  name: string;
  description: string;
  hypothesis: string;
  tags: string[];
  capture_prompts: boolean;
  num_tournaments: number;
  max_hands_per_tournament: number;
  num_players: number;
  starting_stack: number;
  big_blind: number;
  model: string;
  provider: string;
  personalities: string[] | null;
  random_seed: number | null;
  prompt_config: Partial<PromptConfig> | null;
  player_configs: PlayerConfig[] | null;
  // A/B testing support
  control: ControlConfig | null;
  variants: VariantConfig[] | null;
}

export interface ExperimentSummary {
  id: number;
  name: string;
  description: string;
  hypothesis: string;
  tags: string[];
  status: ExperimentStatus;
  created_at: string;
  completed_at: string | null;
  games_count: number;
  num_tournaments: number;
  model: string | null;
  provider: string | null;
  summary: ExperimentResultSummary | null;
}

/**
 * Latency metrics for API calls.
 */
export interface LatencyMetrics {
  avg_ms: number;
  p50_ms: number;
  p95_ms: number;
  p99_ms: number;
  count: number;
}

/**
 * Progress tracking for a variant.
 */
export interface VariantProgress {
  current_hands: number;
  max_hands: number;
  games_count: number;
  games_expected: number;
  progress_pct: number;
}

/**
 * Decision quality metrics.
 */
export interface DecisionQuality {
  total: number;
  correct: number;
  correct_pct: number;
  mistakes: number;
  avg_ev_lost: number;
}

/**
 * Cost metrics for API usage tracking.
 */
export interface CostMetrics {
  total_cost: number;
  total_calls: number;
  avg_cost_per_call: number;
  by_model: Record<string, { cost: number; calls: number }>;
  avg_cost_per_decision: number;
  total_decisions: number;
  cost_per_hand: number;
  total_hands: number;
}

/**
 * Live stats for a single variant during experiment execution.
 */
export interface VariantLiveStats {
  latency_metrics: LatencyMetrics | null;
  decision_quality: DecisionQuality | null;
  progress: VariantProgress;
  cost_metrics: CostMetrics | null;
}

/**
 * Unified live stats response from API.
 */
export interface LiveStats {
  by_variant: Record<string, VariantLiveStats>;
  overall: VariantLiveStats | null;
}

/**
 * Per-variant summary statistics for A/B testing experiments.
 */
export interface VariantResultSummary {
  tournaments: number;
  total_hands: number;
  total_api_calls: number;
  total_duration_seconds: number;
  avg_hands_per_tournament: number;
  winners: Record<string, number>;
  model_config: {
    model: string;
    provider: string;
  };
  decision_quality?: {
    total_decisions: number;
    correct: number;
    marginal: number;
    mistakes: number;
    correct_pct: number;
    mistake_pct: number;
    avg_ev_lost: number;
  };
  latency_metrics?: LatencyMetrics;
}

export interface ExperimentResultSummary {
  tournaments: number;
  total_hands: number;
  total_api_calls: number;
  total_duration_seconds: number;
  avg_hands_per_tournament: number;
  winners: Record<string, number>;
  decision_quality?: {
    total_decisions: number;
    correct: number;
    marginal: number;
    mistakes: number;
    correct_pct: number;
    mistake_pct: number;
    avg_ev_lost: number;
  };
  // Per-variant stats for A/B testing experiments
  variants?: Record<string, VariantResultSummary>;
}

export interface ExperimentDetail extends ExperimentSummary {
  notes: string | null;
  config: ExperimentConfig;
}

export interface ExperimentGame {
  id: number;
  game_id: string;
  variant: string | null;
  variant_config: Record<string, unknown> | null;
  tournament_number: number;
  created_at: string;
}

export interface DecisionStats {
  total: number;
  correct: number;
  marginal: number;
  mistake: number;
  correct_pct: number;
  avg_ev_lost: number;
  by_player: Record<string, {
    total: number;
    correct: number;
    correct_pct: number;
    avg_ev_lost: number;
  }>;
}

export interface ChatMessage {
  role: 'user' | 'assistant';
  content: string;
}

export interface ChatResponse {
  success: boolean;
  response: string;
  session_id: string;
  config_updates: Partial<ExperimentConfig> | null;
  merged_config: ExperimentConfig;
  config_complete: boolean;
}

export interface QuickPrompt {
  id: string;
  label: string;
  prompt: string;
}

export interface ValidationResult {
  valid: boolean;
  errors: string[];
  warnings: string[];
}

export const DEFAULT_EXPERIMENT_CONFIG: ExperimentConfig = {
  name: '',
  description: '',
  hypothesis: '',
  tags: [],
  capture_prompts: true,
  num_tournaments: 1,
  max_hands_per_tournament: 100,
  num_players: 4,
  starting_stack: 10000,
  big_blind: 100,
  model: 'gpt-5-nano',
  provider: 'openai',
  personalities: null,
  random_seed: null,
  prompt_config: null,
  player_configs: null,
  control: null,
  variants: null,
};

export const DEFAULT_PROMPT_CONFIG: PromptConfig = {
  pot_odds: true,
  hand_strength: true,
  session_memory: true,
  opponent_intel: true,
  strategic_reflection: true,
  chattiness: true,
  emotional_state: true,
  tilt_effects: true,
  mind_games: true,
  persona_response: true,
  memory_keep_exchanges: 0,
};
