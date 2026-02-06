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

/**
 * Returns true when the game is in an active betting phase where
 * the current player highlight and action buttons should be shown.
 */
export function isBettingPhase(phase: string | undefined | null, runItOut: boolean): boolean {
  return Boolean(phase) &&
    !runItOut &&
    !NON_BETTING_PHASES.includes(phase as NonBettingPhase);
}
