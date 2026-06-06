import type { ReactNode } from 'react';

// ============================================
// UnifiedSettings — shared types
// ============================================

export type SettingsCategory =
  | 'models'
  | 'capture'
  | 'storage'
  | 'pricing'
  | 'appearance'
  | 'alerting';

export interface CategoryConfig {
  id: SettingsCategory;
  label: string;
  description: string;
  icon: ReactNode;
}

// Model types
export interface Model {
  id: number;
  provider: string;
  model: string;
  enabled: boolean;
  user_enabled: boolean;
  display_name: string | null;
  notes: string | null;
  supports_reasoning: boolean;
  supports_json_mode: boolean;
  supports_image_gen: boolean;
  sort_order: number;
  updated_at: string;
}

// Model visibility states
export type ModelVisibility = 'off' | 'system' | 'users';

// Settings types
export interface SettingConfig {
  value: string;
  options?: string[];
  type?: string;
  description: string;
  env_default: string;
  is_db_override: boolean;
}

// Secret setting: `value`/`env_default` arrive masked; `configured` says set.
export interface WebhookSetting extends SettingConfig {
  configured?: boolean;
  sensitive?: boolean;
}

export interface CaptureSettingsData {
  LLM_PROMPT_CAPTURE: SettingConfig;
  LLM_PROMPT_RETENTION_DAYS: SettingConfig;
}

// System settings include model configurations
export interface SystemSettingsData {
  DEFAULT_PROVIDER: SettingConfig;
  DEFAULT_MODEL: SettingConfig;
  FAST_PROVIDER: SettingConfig;
  FAST_MODEL: SettingConfig;
  NANO_PROVIDER: SettingConfig;
  NANO_MODEL: SettingConfig;
  IMAGE_PROVIDER: SettingConfig;
  IMAGE_MODEL: SettingConfig;
  ASSISTANT_PROVIDER: SettingConfig;
  ASSISTANT_MODEL: SettingConfig;
}

export interface CaptureStats {
  total: number;
  by_call_type?: Record<string, number>;
  by_provider?: Record<string, number>;
}

// Storage types
export interface CategoryStats {
  rows: number;
  bytes: number;
  percentage: number;
}

export interface StorageStats {
  total_bytes: number;
  total_mb: number;
  categories: Record<string, CategoryStats>;
  tables: Record<string, { rows: number; bytes: number }>;
}

export interface AlertState {
  type: 'success' | 'error' | 'info';
  message: string;
}

/** Callback passed to each section to raise the orchestrator's toast. */
export type ShowAlert = (type: AlertState['type'], message: string) => void;

export interface UnifiedSettingsProps {
  embedded?: boolean;
  initialCategory?: SettingsCategory;
  onCategoryChange?: (category: SettingsCategory) => void;
}
