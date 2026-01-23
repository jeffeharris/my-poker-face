export interface ConversationMessage {
  role: 'user' | 'assistant' | 'system';
  content: string;
}

export interface PromptCapture {
  id: number;
  created_at: string;
  game_id: string;
  player_name: string;
  hand_number: number | null;
  phase: string;
  action_taken: string | null;
  system_prompt: string;
  user_message: string;
  ai_response: string;
  conversation_history: ConversationMessage[] | null;
  pot_total: number | null;
  cost_to_call: number | null;
  pot_odds: number | null;
  player_stack: number | null;
  community_cards: string[] | null;
  player_hand: string[] | null;
  valid_actions: string[] | null;
  raise_amount: number | null;
  provider: string | null;
  model: string | null;
  reasoning_effort: string | null;
  latency_ms: number | null;
  input_tokens: number | null;
  output_tokens: number | null;
  cached_tokens: number | null;
  reasoning_tokens: number | null;
  estimated_cost: number | null;
  tags: string[];
  notes: string | null;
  raw_api_response: string | null;
  labels?: Array<{ label: string; label_type: string; created_at: string }>;
}

export interface CaptureStats {
  total: number;
  by_action: Record<string, number>;
  by_phase: Record<string, number>;
  suspicious_folds: number;
}

// Label statistics - mapping from label name to count
export type LabelStats = Record<string, number>;

export interface CaptureListResponse {
  success: boolean;
  captures: PromptCapture[];
  total: number;
  stats: CaptureStats;
  label_stats?: LabelStats;
}

export interface ReplayResponse {
  success: boolean;
  original_response: string;
  new_response: string;
  provider_used: string;
  model_used: string;
  reasoning_effort_used?: string;
  latency_ms: number | null;
  messages_count?: number;
  used_history?: boolean;
  error?: string;
}

export interface CaptureFilters {
  game_id?: string;
  player_name?: string;
  action?: string;
  phase?: string;
  min_pot_odds?: number;
  max_pot_odds?: number;
  min_pot_size?: number;
  max_pot_size?: number;
  min_big_blind?: number;
  max_big_blind?: number;
  tags?: string[];
  labels?: string[];
  labelMatchAll?: boolean;
  limit?: number;
  offset?: number;
}

export interface DecisionAnalysis {
  id: number;
  created_at: string;
  game_id: string;
  player_name: string;
  hand_number: number | null;
  phase: string | null;
  player_position: string | null;  // Hero's table position (button, UTG, etc.)
  pot_total: number | null;
  cost_to_call: number | null;
  player_stack: number | null;
  num_opponents: number | null;
  player_hand: string | null;
  community_cards: string | null;
  action_taken: string | null;
  raise_amount: number | null;
  equity: number | null;
  equity_vs_ranges: number | null;  // Equity vs position-based ranges
  opponent_positions: string | null; // JSON array of opponent positions
  required_equity: number | null;
  ev_call: number | null;
  optimal_action: string | null;
  decision_quality: string | null;
  ev_lost: number | null;
  analyzer_version: string | null;
  processing_time_ms: number | null;
}

export interface DecisionAnalysisStats {
  total: number;
  total_ev_lost: number;
  avg_equity: number | null;
  avg_equity_vs_ranges: number | null;  // Average equity vs position-based ranges
  avg_processing_ms: number | null;
  mistakes: number;
  correct: number;
  by_quality: Record<string, number>;
  by_action: Record<string, number>;
}

// Interrogation mode types
export type DebugMode = 'view' | 'replay' | 'interrogate';

export interface InterrogationMessage {
  id: string;
  role: 'user' | 'assistant' | 'context';
  content: string;
  timestamp: string;
}

export interface InterrogationResponse {
  success: boolean;
  response: string;
  session_id: string;
  messages_count: number;
  provider_used: string;
  model_used: string;
  reasoning_effort_used?: string;
  latency_ms: number | null;
  error?: string;
}

// Re-export ProviderInfo from shared types for backward compatibility
export type { ProviderInfo } from '../../../types/llm';
