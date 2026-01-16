/**
 * Types for the Prompt Playground component.
 */

export interface PlaygroundCapture {
  id: number;
  created_at: string;
  game_id: string | null;
  player_name: string | null;
  hand_number: number | null;
  phase: string;
  call_type: string;
  action_taken: string | null;
  model: string | null;
  provider: string | null;
  reasoning_effort: string | null;
  latency_ms: number | null;
  input_tokens: number | null;
  output_tokens: number | null;
  tags: string[];
  notes: string | null;
}

export interface PlaygroundCaptureDetail extends PlaygroundCapture {
  system_prompt: string;
  user_message: string;
  ai_response: string;
  conversation_history: ConversationMessage[] | null;
  raw_api_response: string | null;
  cached_tokens: number | null;
  reasoning_tokens: number | null;
  estimated_cost: number | null;
}

export interface ConversationMessage {
  role: 'user' | 'assistant' | 'system';
  content: string;
}

export interface PlaygroundStats {
  total: number;
  by_call_type: Record<string, number>;
  by_provider: Record<string, number>;
}

export interface PlaygroundFilters {
  call_type?: string;
  provider?: string;
  date_from?: string;
  date_to?: string;
  limit?: number;
  offset?: number;
}

export interface PlaygroundListResponse {
  success: boolean;
  captures: PlaygroundCapture[];
  total: number;
  stats: PlaygroundStats;
}

export interface ReplayResponse {
  success: boolean;
  original_response: string;
  new_response: string;
  provider_used: string;
  model_used: string;
  reasoning_effort_used?: string;
  input_tokens: number;
  output_tokens: number;
  latency_ms: number | null;
  messages_count?: number;
  used_history?: boolean;
  error?: string;
}

export type PlaygroundMode = 'view' | 'replay';

// Template types
export interface TemplateSummary {
  name: string;
  version: string;
  section_count: number;
  hash: string;
  variables: string[];
}

export interface PromptTemplate {
  name: string;
  version: string;
  sections: Record<string, string>;
  hash: string;
  variables: string[];
}

export interface TemplatePreviewResponse {
  success: boolean;
  rendered: string | null;
  render_error: string | null;
  required_variables: string[];
  missing_variables: string[];
}

export interface TemplateUpdateResponse {
  success: boolean;
  message?: string;
  new_hash?: string;
  new_version?: string;
  error?: string;
}
