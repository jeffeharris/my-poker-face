import { useCallback, useEffect, useState } from 'react';
import { adminFetch } from '../../../../utils/api';
import { logger } from '../../../../utils/logger';
import type { AlertState } from '../types';

interface StakerProfileShape {
  willing: boolean;
  max_loan_pct_of_bankroll: number;
  floor_anchor: number;
  rate_anchor: number;
  respect_floor: number;
  heat_ceiling: number;
}

interface StakerProfileResponse {
  success?: boolean;
  name?: string;
  personality_id?: string;
  /** Effective profile (per-field fallback to STAKER_PROFILE_DEFAULTS). */
  profile?: StakerProfileShape;
  /** Sub-dict actually stored in config_json — used to detect which
   *  fields are hand-tuned vs defaulted. null when the personality
   *  has no explicit staker_profile (everything is using the default). */
  explicit?: Partial<StakerProfileShape> | null;
  defaults?: StakerProfileShape;
  error?: string;
}

interface StakerSideProfileSectionProps {
  personalityName: string;
  showAlert: (type: AlertState['type'], message: string) => void;
}

type KnobField = keyof Omit<StakerProfileShape, 'willing'>;

interface KnobRowProps {
  label: string;
  field: KnobField;
  min: number;
  max: number;
  step: number;
  hint: string;
  value: number;
  explicit: boolean;
  disabled: boolean;
  onChange: (field: KnobField, value: number) => void;
}

/** Reusable slider+number row. Lifted to module scope (was re-created on
 *  every parent render) so all six knobs share one layout without
 *  re-mounting the inputs each keystroke. */
function KnobRow({
  label,
  field,
  min,
  max,
  step,
  hint,
  value,
  explicit,
  disabled,
  onChange,
}: KnobRowProps) {
  return (
    <div style={{ marginBottom: 12 }}>
      <div
        style={{
          display: 'flex',
          alignItems: 'baseline',
          justifyContent: 'space-between',
          marginBottom: 4,
        }}
      >
        <label style={{ fontWeight: 600 }}>{label}</label>
        <span style={{ fontVariantNumeric: 'tabular-nums', fontWeight: 600 }}>
          {value.toFixed(2)}
          {!explicit && (
            <span style={{ marginLeft: 6, fontSize: 11, color: '#aaa', fontWeight: 'normal' }}>
              (default)
            </span>
          )}
        </span>
      </div>
      <input
        type="range"
        min={min * 100}
        max={max * 100}
        step={step * 100}
        value={Math.round(value * 100)}
        onChange={(e) => onChange(field, Number(e.target.value) / 100)}
        disabled={disabled}
        style={{ width: '100%' }}
      />
      <p className="admin-help-text" style={{ marginTop: 2, marginBottom: 0, fontSize: 11.5 }}>
        {hint}
      </p>
    </div>
  );
}

/** Render the per-personality STAKER profile editor — what this AI
 *  offers when OTHER players ask them for a stake-up loan. Mirrors
 *  the borrower side (`StakingProfileSection`) but with the six lender
 *  knobs instead of the willingness threshold.
 *
 *  No ego-derivation here — every field is either explicitly set in
 *  config_json or falls back to STAKER_PROFILE_DEFAULTS at load time.
 *  The route does a full-replacement PUT, so saving writes all six
 *  fields whether they were tuned or not. */
export function StakerSideProfileSection({
  personalityName,
  showAlert,
}: StakerSideProfileSectionProps) {
  const [data, setData] = useState<StakerProfileResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [editing, setEditing] = useState<StakerProfileShape | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const response = await adminFetch(
        `/api/personality/${encodeURIComponent(personalityName)}/staker-profile`
      );
      const body = (await response.json()) as StakerProfileResponse;
      if (body.success && body.profile) {
        setData(body);
        setEditing({ ...body.profile });
      } else {
        showAlert('error', body.error || 'Failed to load staker profile');
      }
    } catch (e) {
      logger.error('Failed to load staker profile', e);
      showAlert('error', 'Error loading staker profile');
    } finally {
      setLoading(false);
    }
  }, [personalityName, showAlert]);

  useEffect(() => {
    void load();
  }, [load]);

  const handleSave = useCallback(async () => {
    if (!editing) return;
    setSaving(true);
    try {
      const response = await adminFetch(
        `/api/personality/${encodeURIComponent(personalityName)}/staker-profile`,
        { method: 'PUT', body: JSON.stringify(editing) }
      );
      const body = (await response.json()) as StakerProfileResponse;
      if (body.success && body.profile) {
        setData(body);
        setEditing({ ...body.profile });
        showAlert('success', 'Staker profile saved');
      } else {
        showAlert('error', body.error || 'Failed to save staker profile');
      }
    } catch (e) {
      logger.error('Failed to save staker profile', e);
      showAlert('error', 'Error saving staker profile');
    } finally {
      setSaving(false);
    }
  }, [editing, personalityName, showAlert]);

  if (loading) {
    return <p className="admin-help-text">Loading staker profile…</p>;
  }
  if (!data || !editing || !data.profile || !data.defaults) {
    return <p className="admin-help-text">No staker profile data available.</p>;
  }

  const hasChanges =
    editing.willing !== data.profile.willing ||
    editing.max_loan_pct_of_bankroll !== data.profile.max_loan_pct_of_bankroll ||
    editing.floor_anchor !== data.profile.floor_anchor ||
    editing.rate_anchor !== data.profile.rate_anchor ||
    editing.respect_floor !== data.profile.respect_floor ||
    editing.heat_ceiling !== data.profile.heat_ceiling;

  // Helper — is `field` explicitly tuned in config_json, or defaulted?
  const isExplicit = (field: keyof StakerProfileShape): boolean => {
    return !!(data.explicit && field in data.explicit);
  };

  const onKnobChange = (field: KnobField, value: number) => {
    setEditing((prev) => (prev ? { ...prev, [field]: value } : prev));
  };

  const knobDisabled = saving || !editing.willing;

  return (
    <div className="pm-bankroll-knobs">
      <p className="admin-help-text" style={{ marginTop: 0 }}>
        How this personality behaves when ANOTHER player (AI or human) asks them for a stake-up
        loan. Six knobs that shape the offer terms and the relationship gates that have to clear
        before they'll lend.
      </p>

      <label
        className="admin-checkbox-row"
        style={{ display: 'flex', alignItems: 'center', gap: 8 }}
      >
        <input
          type="checkbox"
          checked={editing.willing}
          onChange={(e) => setEditing({ ...editing, willing: e.target.checked })}
          disabled={saving}
        />
        <span>Willing to stake other players</span>
      </label>
      <p className="admin-help-text" style={{ marginTop: 4, marginBottom: 16 }}>
        Unchecking blocks all offers from this personality regardless of the knobs below. Chaos /
        hostile personalities (Mime, Cheshire Cat) refuse outright.
      </p>

      <KnobRow
        label="Max loan (% of bankroll)"
        field="max_loan_pct_of_bankroll"
        min={0}
        max={0.3}
        step={0.01}
        hint="Largest loan size as a fraction of their projected bankroll. Generous = 0.10–0.20, cautious = 0.03–0.07."
        value={editing.max_loan_pct_of_bankroll}
        explicit={isExplicit('max_loan_pct_of_bankroll')}
        disabled={knobDisabled}
        onChange={onKnobChange}
      />
      <KnobRow
        label="Floor anchor (repayment multiple)"
        field="floor_anchor"
        min={1.0}
        max={2.0}
        step={0.05}
        hint="Repayment floor multiple — 1.00 = par, 1.20 = +20%. Saintly = 1.00–1.10, sharks = 1.30–1.50."
        value={editing.floor_anchor}
        explicit={isExplicit('floor_anchor')}
        disabled={knobDisabled}
        onChange={onKnobChange}
      />
      <KnobRow
        label="Rate anchor (cut after floor)"
        field="rate_anchor"
        min={0}
        max={0.6}
        step={0.01}
        hint="Sponsor's cut of post-floor winnings. Gentle = 0.10–0.20, ruthless = 0.35–0.50."
        value={editing.rate_anchor}
        explicit={isExplicit('rate_anchor')}
        disabled={knobDisabled}
        onChange={onKnobChange}
      />
      <KnobRow
        label="Respect floor"
        field="respect_floor"
        min={-1.0}
        max={1.0}
        step={0.05}
        hint="Minimum relationship-respect needed before lending. More negative = lends to almost anyone."
        value={editing.respect_floor}
        explicit={isExplicit('respect_floor')}
        disabled={knobDisabled}
        onChange={onKnobChange}
      />
      <KnobRow
        label="Heat ceiling"
        field="heat_ceiling"
        min={0}
        max={1.0}
        step={0.05}
        hint="Maximum active conflict tolerated while lending. 1.00 = never refuses on heat alone; 0.00 = any heat blocks."
        value={editing.heat_ceiling}
        explicit={isExplicit('heat_ceiling')}
        disabled={knobDisabled}
        onChange={onKnobChange}
      />

      <div style={{ display: 'flex', gap: 8, marginTop: 12, flexWrap: 'wrap' }}>
        <button
          type="button"
          className="admin-btn admin-btn--secondary"
          onClick={() => data.defaults && setEditing({ ...data.defaults })}
          disabled={saving}
          title="Reset all knobs to STAKER_PROFILE_DEFAULTS."
        >
          Reset to defaults
        </button>
        <button
          type="button"
          className="admin-btn admin-btn--secondary"
          onClick={() => data.profile && setEditing({ ...data.profile })}
          disabled={saving || !hasChanges}
        >
          Discard changes
        </button>
        <button
          type="button"
          className="admin-btn admin-btn--primary"
          onClick={() => void handleSave()}
          disabled={saving || !hasChanges}
          style={{ marginLeft: 'auto' }}
        >
          {saving ? 'Saving…' : 'Save staker profile'}
        </button>
      </div>
    </div>
  );
}
