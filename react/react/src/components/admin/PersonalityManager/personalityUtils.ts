import type { PersonalityAnchors } from './types';

/** Default psychology anchors for a freshly-created persona. Includes the
 *  10th anchor (self_belief) so manual creates seed a sensible default. */
export function getDefaultAnchors(): PersonalityAnchors {
  return {
    baseline_aggression: 0.5,
    baseline_looseness: 0.3,
    ego: 0.5,
    poise: 0.7,
    expressiveness: 0.5,
    risk_identity: 0.5,
    adaptation_bias: 0.5,
    baseline_energy: 0.5,
    self_belief: 0.5,
    recovery_rate: 0.15,
  };
}

export function classifyArchetype(
  looseness: number,
  aggression: number
): { key: string; label: string } {
  if (looseness < 0.45) {
    return aggression < 0.5
      ? { key: 'tight_passive', label: 'Rock' }
      : { key: 'tight_aggressive', label: 'TAG' };
  } else if (looseness > 0.65) {
    return aggression < 0.5
      ? { key: 'loose_passive', label: 'Fish' }
      : { key: 'loose_aggressive', label: 'LAG' };
  }
  return { key: 'default', label: 'Balanced' };
}
