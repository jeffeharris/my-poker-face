// ============================================
// PersonalityManager — shared types & constants
// ============================================

export interface PersonalityAnchors {
  baseline_aggression: number;
  baseline_looseness: number;
  ego: number;
  poise: number;
  expressiveness: number;
  risk_identity: number;
  adaptation_bias: number;
  baseline_energy: number;
  /** 10th psychology anchor (bravado / delusion dial). The generator emits it
   *  and the strategy pipeline reads it; surfaced here so admins can tune it. */
  self_belief: number;
  recovery_rate: number;
}

/** A single exploitable-habit entry: [registered tendency name, strength 0..1]. */
export type SpotTendency = [string, number];

export interface PersonalityData {
  play_style?: string;
  default_confidence?: string;
  default_attitude?: string;
  anchors?: PersonalityAnchors;
  verbal_tics?: string[];
  physical_tics?: string[];
  avatar_description?: string;
  /** Named skill tier (see SKILL_TIERS). Empty/absent = derived from
   *  anchors.adaptation_bias by the backend. */
  skill?: string;
  /** Exploitable-habit texture layer; up to MAX_SPOT_TENDENCIES entries. */
  spot_tendencies?: SpotTendency[];
  // NOTE: other persona fields (visual_identity, rule_strategy, fish_leak,
  // adaptive_overbet, nickname, id, …) are intentionally not modelled here.
  // They are preserved on save via the form buffer's shallow copy, just not
  // editable in this UI yet.
  [key: string]: unknown;
}

export interface EmotionImage {
  emotion: string;
  url: string | null;
  hasFullImage: boolean;
}

export interface AlertState {
  type: 'success' | 'error' | 'info';
  message: string;
}

export interface ModalState {
  type: 'delete' | 'regenerate' | 'create' | null;
  data?: unknown;
}

// ============================================
// Skill tiers (mirror poker/strategy/skill_tiers.py)
// ============================================

export interface SkillTierOption {
  value: string;
  label: string;
  hint: string;
}

/** Strongest → weakest. Mirrors SKILL_TIERS; DEFAULT is 'shark'. */
export const SKILL_TIERS: readonly SkillTierOption[] = [
  {
    value: 'shark',
    label: 'Shark',
    hint: 'Full reads, balance, and defense — today’s ceiling bot.',
  },
  { value: 'reg', label: 'Reg', hint: 'Solid: softer reads, still balanced and defends.' },
  {
    value: 'weak_reg',
    label: 'Weak Reg',
    hint: 'Leaky regular — reduced exploitation and defense.',
  },
  { value: 'rec', label: 'Recreational', hint: 'Recreational player — minimal reads and balance.' },
] as const;

/** adaptation_bias → tier band cutoffs (mirror skill_tier_for_adaptation_bias). */
const SKILL_TIER_CUTOFFS: readonly [number, string][] = [
  [0.6, 'shark'],
  [0.45, 'reg'],
  [0.225, 'weak_reg'],
];

/** Map a persona's adaptation_bias to its derived skill tier. */
export function skillTierForAdaptationBias(adaptationBias: number | undefined | null): string {
  if (adaptationBias === undefined || adaptationBias === null) return 'reg';
  for (const [cutoff, tier] of SKILL_TIER_CUTOFFS) {
    if (adaptationBias >= cutoff) return tier;
  }
  return 'rec';
}

export function skillTierLabel(value: string): string {
  return SKILL_TIERS.find((t) => t.value === value)?.label ?? value;
}

// ============================================
// Spot tendencies (mirror poker/strategy/spot_tendencies.py registry)
// ============================================

export const MAX_SPOT_TENDENCIES = 3;

/** The 9 registered tendencies (REGISTERED_SPOT_TENDENCIES). Names outside
 *  this set are dropped server-side, so the editor only offers these. */
export const REGISTERED_SPOT_TENDENCIES: readonly SkillTierOption[] = [
  { value: 'slowplay', label: 'Slowplay', hint: 'Trap with strong hands — under-bet the nuts.' },
  { value: 'auto_cbet', label: 'Auto C-Bet', hint: 'Over-bet the flop when holding initiative.' },
  { value: 'fit_or_fold', label: 'Fit-or-Fold', hint: 'Over-fold weak/air to a flop c-bet.' },
  { value: 'give_up_turn', label: 'Give Up Turn', hint: 'Dampen aggression on the turn barrel.' },
  { value: 'sticky', label: 'Sticky', hint: 'Over-call weak made hands down to the river.' },
  { value: 'over_bluff', label: 'Over-Bluff', hint: 'Bluff the river too often with air.' },
  { value: 'under_bluff', label: 'Under-Bluff', hint: 'Rarely bluff the river with air.' },
  {
    value: 'over_fold_2nd_barrel',
    label: 'Over-Fold 2nd Barrel',
    hint: 'Fold marginal made hands to a sustained value line.',
  },
  {
    value: 'donk_when_weak',
    label: 'Donk When Weak',
    hint: 'Lead (donk) out of position with weak hands into the aggressor.',
  },
];
