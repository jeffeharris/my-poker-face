/**
 * Game mode options shared across components.
 * Keep in sync with VALID_GAME_MODES in flask_app/routes/game_routes.py
 * and ALLOWED_PLAYER_MODES in flask_app/routes/personality_routes.py.
 * Note: "pro" mode is available for Custom Game but excluded from themed games.
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
