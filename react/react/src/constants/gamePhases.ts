/**
 * Phases during which no betting actions are possible.
 * Active player highlighting and action buttons should be suppressed during these phases.
 *
 * Backend source: poker/poker_state_machine.py (PokerPhase enum)
 */
export const NON_BETTING_PHASES = [
  'EVALUATING_HAND',
  'HAND_OVER',
  'SHOWDOWN',
  'GAME_OVER',
] as const;

export type NonBettingPhase = typeof NON_BETTING_PHASES[number];
