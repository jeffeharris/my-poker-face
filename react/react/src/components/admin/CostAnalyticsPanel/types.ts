// API shapes for the cost-analytics admin endpoints
// (flask_app/routes/cost_analytics_routes.py).

export type CostRange = '24h' | '7d' | '30d' | 'all';

export interface UsageSummary {
  total_calls: number;
  total_cost: number;
  avg_latency: number;
  error_rate: number;
}

export interface OwnerCost {
  owner_id: string;
  total_cost: number;
  total_calls: number;
  image_calls: number;
  error_calls: number;
  input_tokens: number;
  output_tokens: number;
}

export interface CallTypeCost {
  call_type: string;
  total_cost: number;
  total_calls: number;
  avg_latency: number;
  input_tokens: number;
  output_tokens: number;
  cached_tokens: number;
  reasoning_tokens: number;
  image_count: number;
}

export interface ModelCost {
  provider: string;
  model: string;
  total_cost: number;
  total_calls: number;
  input_tokens: number;
  output_tokens: number;
}

export interface GameCost {
  game_id: string;
  owner_id: string;
  total_cost: number;
  total_calls: number;
  max_hand: number | null;
}

export interface TimeseriesPoint {
  period: string;
  total_cost: number;
  total_calls: number;
}

export interface UncostedModel {
  provider: string;
  model: string;
  calls: number;
  last_seen: string;
}

export interface UncostedCalls {
  total: number;
  by_model: UncostedModel[];
}

export interface CostOverview {
  range: CostRange;
  summary: UsageSummary;
  by_owner: OwnerCost[];
  by_call_type: CallTypeCost[];
  by_model: ModelCost[];
  by_game: GameCost[];
  uncosted: UncostedCalls;
  timeseries: TimeseriesPoint[];
}

export interface OwnerDetail {
  range: CostRange;
  owner_id: string;
  total_cost: number;
  total_calls: number;
  by_call_type: CallTypeCost[];
  by_model: ModelCost[];
  by_game: GameCost[];
  timeseries: TimeseriesPoint[];
}

export interface UsageCall {
  id: number;
  created_at: string;
  owner_id: string | null;
  player_name: string | null;
  game_id: string | null;
  hand_number: number | null;
  call_type: string;
  provider: string;
  model: string;
  reasoning_effort: string | null;
  input_tokens: number;
  output_tokens: number;
  cached_tokens: number;
  reasoning_tokens: number;
  image_count: number;
  image_size: string | null;
  latency_ms: number | null;
  status: string;
  finish_reason: string | null;
  error_code: string | null;
  estimated_cost: number | null;
}

export interface CallsResponse {
  range: CostRange;
  count: number;
  calls: UsageCall[];
}
