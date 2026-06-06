import { SKILL_TIERS, skillTierForAdaptationBias, skillTierLabel } from './types';

interface SkillTierSelectProps {
  /** The persona's pinned skill tier, or '' / undefined when auto-derived. */
  value: string;
  /** Current anchors.adaptation_bias — drives the derived default. */
  adaptationBias: number;
  onChange: (value: string) => void;
}

/**
 * Skill-tier selector. Empty value means "auto" — the backend derives the
 * tier from adaptation_bias. Mirrors the borrower-profile pattern of showing
 * the derived default alongside an explicit override.
 */
export function SkillTierSelect({ value, adaptationBias, onChange }: SkillTierSelectProps) {
  const derived = skillTierForAdaptationBias(adaptationBias);
  const effective = value || derived;
  const hint = SKILL_TIERS.find((t) => t.value === effective)?.hint ?? '';

  return (
    <div className="admin-form-group">
      <label className="admin-label" htmlFor="skill-tier">
        Skill Tier
      </label>
      <select
        id="skill-tier"
        className="admin-input admin-select"
        value={value}
        onChange={(e) => onChange(e.target.value)}
      >
        <option value="">Auto — derive from adaptation ({skillTierLabel(derived)})</option>
        {SKILL_TIERS.map((tier) => (
          <option key={tier.value} value={tier.value}>
            {tier.label}
          </option>
        ))}
      </select>
      <p className="admin-help-text">
        {value ? (
          <>
            Pinned to <strong>{skillTierLabel(value)}</strong>.
          </>
        ) : (
          <>
            Auto — derives <strong>{skillTierLabel(derived)}</strong> from adaptation_bias{' '}
            {adaptationBias.toFixed(2)}.
          </>
        )}{' '}
        {hint}
      </p>
    </div>
  );
}
