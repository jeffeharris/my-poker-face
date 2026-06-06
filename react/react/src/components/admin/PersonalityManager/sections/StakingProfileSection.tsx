import { useCallback, useEffect, useState } from 'react';
import { adminFetch } from '../../../../utils/api';
import { logger } from '../../../../utils/logger';
import type { AlertState } from '../types';

interface BorrowerProfileResponse {
  success?: boolean;
  name?: string;
  personality_id?: string;
  willing?: boolean;
  /** Effective threshold the staking engine uses — explicit override
   *  if set in config_json, else ego-derived. */
  willingness_threshold?: number;
  /** The explicit override from config_json, or null if none set
   *  (i.e. ego derivation is in effect). */
  willingness_threshold_explicit?: number | null;
  /** What ego derivation would yield, regardless of override. Powers
   *  the "Use ego-derived default" reset button. */
  ego_derived_threshold?: number;
  ego?: number;
  defaults?: { willing: boolean; willingness_threshold: number };
  error?: string;
}

interface StakingProfileSectionProps {
  personalityName: string;
  showAlert: (type: AlertState['type'], message: string) => void;
}

/** Render the per-personality borrower profile editor — the AI's
 *  willingness to BE staked by a player, and the relationship-axes
 *  trust threshold they require before accepting.
 *
 *  The threshold has two states the admin needs to think about:
 *    - Explicit override: stored in config_json.borrower_profile.
 *      Hand-tuned per-personality for special cases.
 *    - Ego-derived: computed at load time from anchors.ego when no
 *      override is set. Default for the bulk of the roster.
 *
 *  This section makes that distinction visible: show the effective
 *  value, mark whether it's overridden, and offer a one-click reset
 *  to clear the override and revert to ego-derived. */
export function StakingProfileSection({ personalityName, showAlert }: StakingProfileSectionProps) {
  const [data, setData] = useState<BorrowerProfileResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  /** Local edit buffer — separate from `data` so we can show
   *  dirty/pristine state and reset cleanly on a discard. */
  const [editing, setEditing] = useState<{
    willing: boolean;
    threshold: number;
    /** null = clear override (use ego-derived); number = explicit override */
    threshold_override: number | null;
  } | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const response = await adminFetch(
        `/api/personality/${encodeURIComponent(personalityName)}/borrower-profile`
      );
      const body = (await response.json()) as BorrowerProfileResponse;
      if (body.success && body.willing !== undefined && body.willingness_threshold !== undefined) {
        setData(body);
        setEditing({
          willing: body.willing,
          threshold: body.willingness_threshold,
          threshold_override: body.willingness_threshold_explicit ?? null,
        });
      } else {
        showAlert('error', body.error || 'Failed to load staking profile');
      }
    } catch (e) {
      logger.error('Failed to load borrower profile', e);
      showAlert('error', 'Error loading staking profile');
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
        `/api/personality/${encodeURIComponent(personalityName)}/borrower-profile`,
        {
          method: 'PUT',
          body: JSON.stringify({
            willing: editing.willing,
            // Send null when the admin chose "ego-derived" — that
            // clears the explicit override server-side.
            willingness_threshold: editing.threshold_override,
          }),
        }
      );
      const body = (await response.json()) as BorrowerProfileResponse;
      if (body.success && body.willing !== undefined && body.willingness_threshold !== undefined) {
        setData(body);
        setEditing({
          willing: body.willing,
          threshold: body.willingness_threshold,
          threshold_override: body.willingness_threshold_explicit ?? null,
        });
        showAlert('success', 'Staking profile saved');
      } else {
        showAlert('error', body.error || 'Failed to save staking profile');
      }
    } catch (e) {
      logger.error('Failed to save borrower profile', e);
      showAlert('error', 'Error saving staking profile');
    } finally {
      setSaving(false);
    }
  }, [editing, personalityName, showAlert]);

  if (loading) {
    return <p className="admin-help-text">Loading staking profile…</p>;
  }
  if (!data || !editing) {
    return <p className="admin-help-text">No staking profile data available.</p>;
  }

  const isOverride = editing.threshold_override !== null;
  const egoDerived = data.ego_derived_threshold ?? 0.3;
  const hasChanges =
    editing.willing !== data.willing ||
    editing.threshold_override !== (data.willingness_threshold_explicit ?? null);

  // Effective threshold = override if set, else ego-derived. Keep the
  // slider showing the OVERRIDE value while editing so the admin sees
  // exactly what they're committing.
  const effectiveValue = isOverride ? (editing.threshold_override as number) : egoDerived;

  return (
    <div className="pm-bankroll-knobs">
      <p className="admin-help-text" style={{ marginTop: 0 }}>
        Whether this personality accepts stakes from the player and how much goodwill they need
        before saying yes.
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
        <span>Accepts stakes from players</span>
      </label>
      <p className="admin-help-text" style={{ marginTop: 4, marginBottom: 16 }}>
        Stoic / principled personalities (Lincoln, Buddha) refuse outright. Unchecking blocks stake
        offers regardless of the threshold below.
      </p>

      <div
        style={{
          display: 'flex',
          alignItems: 'baseline',
          justifyContent: 'space-between',
          marginBottom: 4,
        }}
      >
        <label htmlFor="willingness-threshold-slider" style={{ fontWeight: 600 }}>
          Willingness threshold
        </label>
        <span style={{ fontVariantNumeric: 'tabular-nums', fontWeight: 600 }}>
          {effectiveValue.toFixed(2)}
          {!isOverride && (
            <span style={{ marginLeft: 6, fontSize: 11, color: '#aaa', fontWeight: 'normal' }}>
              (derived from ego)
            </span>
          )}
          {isOverride && (
            <span style={{ marginLeft: 6, fontSize: 11, color: '#ffd87d', fontWeight: 'normal' }}>
              (override)
            </span>
          )}
        </span>
      </div>
      <input
        id="willingness-threshold-slider"
        type="range"
        min={10}
        max={60}
        step={1}
        value={Math.round(effectiveValue * 100)}
        onChange={(e) => {
          const v = Number(e.target.value) / 100;
          setEditing({ ...editing, threshold_override: v, threshold: v });
        }}
        disabled={saving || !editing.willing}
        style={{ width: '100%' }}
      />
      <div
        style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11, color: '#aaa' }}
      >
        <span>0.10 (easy)</span>
        <span>0.60 (selective)</span>
      </div>
      <p className="admin-help-text" style={{ marginTop: 8, fontSize: 11.5 }}>
        Score = likability × 0.5 + respect × 0.4 − heat × 0.3. The AI accepts iff score &gt;
        threshold (plus a cut penalty when the offer's cut is steep, minus a desperation relief when
        broke and proud).
      </p>

      <div
        style={{
          marginTop: 12,
          padding: '8px 10px',
          background: 'rgba(0,0,0,0.18)',
          borderRadius: 6,
          fontSize: 12,
        }}
      >
        <div style={{ display: 'flex', justifyContent: 'space-between' }}>
          <span style={{ color: '#aaa' }}>Ego anchor</span>
          <span style={{ fontVariantNumeric: 'tabular-nums' }}>{(data.ego ?? 0.5).toFixed(2)}</span>
        </div>
        <div style={{ display: 'flex', justifyContent: 'space-between' }}>
          <span style={{ color: '#aaa' }}>Ego-derived default</span>
          <span style={{ fontVariantNumeric: 'tabular-nums' }}>{egoDerived.toFixed(2)}</span>
        </div>
      </div>

      <div style={{ display: 'flex', gap: 8, marginTop: 12, flexWrap: 'wrap' }}>
        <button
          type="button"
          className="admin-btn admin-btn--secondary"
          onClick={() =>
            setEditing({ ...editing, threshold_override: null, threshold: egoDerived })
          }
          disabled={saving || !isOverride}
          title="Drop the explicit override; threshold will derive from ego."
        >
          Use ego-derived default
        </button>
        <button
          type="button"
          className="admin-btn admin-btn--secondary"
          onClick={() => {
            // Revert local edits to last-saved state.
            setEditing({
              willing: data.willing ?? true,
              threshold: data.willingness_threshold ?? 0.3,
              threshold_override: data.willingness_threshold_explicit ?? null,
            });
          }}
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
          {saving ? 'Saving…' : 'Save staking profile'}
        </button>
      </div>
    </div>
  );
}
