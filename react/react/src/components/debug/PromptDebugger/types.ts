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
}

export interface CaptureStats {
  total: number;
  by_action: Record<string, number>;
  by_phase: Record<string, number>;
  suspicious_folds: number;
}

export interface CaptureListResponse {
  success: boolean;
  captures: PromptCapture[];
  total: number;
  stats: CaptureStats;
}

export interface ReplayResponse {
  success: boolean;
  original_response: string;
  new_response: string;
  model_used: string;
  latency_ms: number | null;
  error?: string;
}

export interface CaptureFilters {
  game_id?: string;
  player_name?: string;
  action?: string;
  phase?: string;
  min_pot_odds?: number;
  max_pot_odds?: number;
  tags?: string[];
  limit?: number;
  offset?: number;
}
