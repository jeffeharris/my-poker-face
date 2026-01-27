/**
 * Shared LLM types used across components.
 */

/**
 * Capabilities of an LLM provider (provider-level defaults).
 */
export interface ProviderCapabilities {
  supports_reasoning: boolean;
  supports_json_mode: boolean;
  supports_image_generation: boolean;
  image_only?: boolean;
}

/**
 * Model-specific capabilities (supplements provider-level).
 */
export interface ModelCapabilities {
  supports_reasoning?: boolean;
  supports_json_mode?: boolean;
  supports_image_generation?: boolean;
  supports_img2img?: boolean;
}

/**
 * Information about an LLM provider and its available models.
 */
export interface ProviderInfo {
  id: string;
  name: string;
  models: string[];
  default_model: string;
  capabilities?: ProviderCapabilities;
  model_capabilities?: Record<string, ModelCapabilities>;
  model_tiers?: Record<string, string>;
}

/**
 * Response format from /api/user-models and /api/system-models endpoints.
 */
export interface LLMProvidersResponse {
  providers: ProviderInfo[];
  default_provider: string;
}

/**
 * Scope for model fetching:
 * - 'user': Models available to end users (enabled=1 AND user_enabled=1)
 * - 'system': Models available to admins/system (enabled=1, ignores user_enabled)
 */
export type ModelScope = 'user' | 'system';

/**
 * LLM configuration for an opponent or player.
 */
export interface OpponentLLMConfig {
  provider: string;
  model: string;
  reasoning_effort?: string;
  game_mode?: string;
}
