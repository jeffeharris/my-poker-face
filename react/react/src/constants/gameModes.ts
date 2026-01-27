/**
 * Game mode options shared across components.
 * Keep in sync with config/game_modes.yaml (the backend source of truth).
 */

export interface GameModeOption {
  value: string;
  label: string;
  description: string;
}

export const GAME_MODES: GameModeOption[] = [
  { value: 'casual', label: 'Casual', description: 'Fun, personality-driven' },
  { value: 'standard', label: 'Standard', description: 'Balanced + GTO awareness' },
  { value: 'competitive', label: 'Competitive', description: 'GTO + trash talk' },
  { value: 'pro', label: 'Pro', description: 'Full GTO, analytical' },
];
