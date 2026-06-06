import { useCallback, useEffect, useState } from 'react';
import { adminFetch } from '../../../../utils/api';
import { logger } from '../../../../utils/logger';
import type { AlertState } from '../types';

interface BankrollKnobs {
  starting_bankroll: number;
  bankroll_rate: number;
  buy_in_multiplier: number;
  stake_comfort_zone: string;
}

interface BankrollKnobsResponse {
  success?: boolean;
  knobs?: BankrollKnobs;
  defaults?: BankrollKnobs;
  current_bankroll?: number | null;
  error?: string;
}

interface BankrollKnobsSectionProps {
  personalityName: string;
  showAlert: (type: AlertState['type'], message: string) => void;
}

const STAKE_COMFORT_OPTIONS = ['$2', '$10', '$50', '$200', '$1000'] as const;

export function BankrollKnobsSection({ personalityName, showAlert }: BankrollKnobsSectionProps) {
  const [knobs, setKnobs] = useState<BankrollKnobs | null>(null);
  const [defaults, setDefaults] = useState<BankrollKnobs | null>(null);
  const [currentBankroll, setCurrentBankroll] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  // Track the last loaded snapshot so we can compute dirtiness — the
  // section saves independently of the main editor's Save button so
  // admins can iterate on knobs without re-saving the whole personality.
  const [original, setOriginal] = useState<BankrollKnobs | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const response = await adminFetch(
        `/api/personality/${encodeURIComponent(personalityName)}/bankroll-knobs`
      );
      const data: BankrollKnobsResponse = await response.json();
      if (data.success && data.knobs && data.defaults) {
        setKnobs(data.knobs);
        setOriginal(data.knobs);
        setDefaults(data.defaults);
        setCurrentBankroll(data.current_bankroll ?? null);
      } else {
        showAlert('error', data.error || 'Failed to load bankroll knobs');
      }
    } catch (e) {
      logger.error('Failed to load bankroll knobs', e);
      showAlert('error', 'Error loading bankroll knobs');
    } finally {
      setLoading(false);
    }
  }, [personalityName, showAlert]);

  useEffect(() => {
    load();
  }, [load]);

  const updateField = <K extends keyof BankrollKnobs>(field: K, value: BankrollKnobs[K]) => {
    setKnobs((prev) => (prev ? { ...prev, [field]: value } : prev));
  };

  const hasChanges =
    !!knobs &&
    !!original &&
    (knobs.starting_bankroll !== original.starting_bankroll ||
      knobs.bankroll_rate !== original.bankroll_rate ||
      knobs.buy_in_multiplier !== original.buy_in_multiplier ||
      knobs.stake_comfort_zone !== original.stake_comfort_zone);

  const handleSave = async () => {
    if (!knobs) return;
    setSaving(true);
    try {
      const response = await adminFetch(
        `/api/personality/${encodeURIComponent(personalityName)}/bankroll-knobs`,
        {
          method: 'PUT',
          body: JSON.stringify(knobs),
        }
      );
      const data: BankrollKnobsResponse = await response.json();
      if (data.success && data.knobs) {
        setKnobs(data.knobs);
        setOriginal(data.knobs);
        showAlert('success', 'Bankroll knobs saved');
      } else {
        showAlert('error', data.error || 'Failed to save bankroll knobs');
      }
    } catch (e) {
      logger.error('Failed to save bankroll knobs', e);
      showAlert('error', 'Error saving bankroll knobs');
    } finally {
      setSaving(false);
    }
  };

  const handleResetToCap = async () => {
    if (!knobs) return;
    // "Reset bankroll to cap" is a testing convenience: it's a write
    // through the same admin route the live bankroll surface uses on
    // the read side, but there's no dedicated endpoint for it in v1.
    // We emulate it by re-saving the existing knobs (which leaves them
    // unchanged) and then telling the admin to seed via a future
    // endpoint. Until that lands, the affordance just refreshes the
    // current_bankroll display so the admin can see the live state.
    await load();
  };

  if (loading) {
    return <p className="admin-help-text">Loading bankroll knobs…</p>;
  }
  if (!knobs || !defaults) {
    return <p className="admin-help-text">No knobs data available.</p>;
  }

  return (
    <div className="pm-bankroll-knobs">
      <p className="admin-help-text" style={{ marginTop: 0 }}>
        Cash-mode bankroll behavior. The cap is a hard ceiling — table winnings above it evaporate
        when the AI cashes out.
      </p>

      <div className="admin-form-group">
        <label className="admin-label">Current live bankroll</label>
        <p className="admin-help-text" style={{ marginTop: 0 }}>
          {currentBankroll !== null
            ? `${currentBankroll.toLocaleString()} chips`
            : 'No bankroll row yet — AI has never sat at a cash table.'}{' '}
          <button
            type="button"
            className="admin-btn admin-btn--secondary"
            style={{ marginLeft: 'var(--space-2)' }}
            onClick={handleResetToCap}
          >
            Refresh
          </button>
        </p>
      </div>

      <div className="admin-form-row">
        <div className="admin-form-group">
          <label className="admin-label" htmlFor="starting_bankroll">
            Starting bankroll
          </label>
          <input
            id="starting_bankroll"
            type="number"
            className="admin-input"
            min={0}
            value={knobs.starting_bankroll}
            onChange={(e) => updateField('starting_bankroll', Number(e.target.value))}
          />
          <p className="admin-help-text">
            Seed bankroll on first sit; default {defaults.starting_bankroll.toLocaleString()}
          </p>
        </div>
        <div className="admin-form-group">
          <label className="admin-label" htmlFor="bankroll_rate">
            Bankroll rate
          </label>
          <input
            id="bankroll_rate"
            type="number"
            className="admin-input"
            min={0}
            value={knobs.bankroll_rate}
            onChange={(e) => updateField('bankroll_rate', Number(e.target.value))}
          />
          <p className="admin-help-text">
            Chips/day passive regen; default {defaults.bankroll_rate}
          </p>
        </div>
      </div>

      <div className="admin-form-row">
        <div className="admin-form-group">
          <label className="admin-label" htmlFor="buy_in_multiplier">
            Buy-in multiplier
          </label>
          <input
            id="buy_in_multiplier"
            type="number"
            className="admin-input"
            step="0.1"
            min={0.1}
            value={knobs.buy_in_multiplier}
            onChange={(e) => updateField('buy_in_multiplier', Number(e.target.value))}
          />
          <p className="admin-help-text">× min_buy_in; default {defaults.buy_in_multiplier}</p>
        </div>
        <div className="admin-form-group">
          <label className="admin-label" htmlFor="stake_comfort_zone">
            Stake comfort zone
          </label>
          <select
            id="stake_comfort_zone"
            className="admin-input"
            value={knobs.stake_comfort_zone}
            onChange={(e) => updateField('stake_comfort_zone', e.target.value)}
          >
            {STAKE_COMFORT_OPTIONS.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
          <p className="admin-help-text">v2 — unused in v1</p>
        </div>
      </div>

      <div style={{ marginTop: 'var(--space-3)' }}>
        <button
          type="button"
          className="admin-btn admin-btn--primary"
          onClick={handleSave}
          disabled={saving || !hasChanges}
        >
          {saving ? 'Saving…' : hasChanges ? 'Save Bankroll Knobs' : 'Saved'}
        </button>
      </div>
    </div>
  );
}
