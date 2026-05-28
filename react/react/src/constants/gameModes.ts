/**
 * Game mode shapes the LLM *prompt* (banter + GTO hints) and therefore only
 * affects LLM-driven controllers (chaos/standard/lean) in Custom Game. The
 * tiered bot — the core engine for Quick Play, themed, and career — does NOT
 * consume game_mode at all, so it is no longer a player-facing difficulty dial.
 *
 * Only "casual" is exposed in the game UI. "standard" and "pro" remain defined
 * in code (config/game_modes.yaml + PromptConfig.from_mode_name) and are still
 * accepted by the backend for experiments / API / old saved games — they are
 * just not selectable from the game. The legacy "competitive" alias (→ pro) is
 * likewise code-only now. See docs/TRIAGE.md for the eventual removal plan.
 *
 * Keep in sync with VALID_GAME_MODES in flask_app/routes/game_routes.py.
 */

export interface GameModeOption {
  value: string;
  label: string;
  description: string;
}

export const GAME_MODES: GameModeOption[] = [
  { value: 'casual', label: 'Casual', description: 'Fun, personality-driven' },
];
