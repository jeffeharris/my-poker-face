import { useState, useEffect, useCallback } from 'react';
import { adminFetch } from '../../../../utils/api';
import type { SettingConfig, ShowAlert } from '../types';

interface GameplaySectionProps {
  showAlert: ShowAlert;
}

const MIN = 0.5;
const MAX = 2.5;
const STEP = 0.1;

/** Coarse, honest label for a given dial value (not a precise prediction). */
function feelLabel(weight: number): string {
  if (weight <= 0.8) return 'Quiet — AIs mostly only speak on the big hands';
  if (weight < 1.15) return 'Reserved';
  if (weight <= 1.45) return 'Balanced (default ≈ a speaker on ~44% of hands)';
  if (weight < 1.85) return 'Chatty';
  return 'Very chatty — talk on most hands';
}

export function GameplaySection({ showAlert }: GameplaySectionProps) {
  const [setting, setSetting] = useState<SettingConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [weight, setWeight] = useState(1.3);
  const [saving, setSaving] = useState(false);

  const fetchData = useCallback(async () => {
    try {
      setLoading(true);
      const res = await adminFetch(`/admin/api/settings`);
      const data = await res.json();
      if (data.success && data.settings?.DRAMA_SPEAK_SCORE_WEIGHT) {
        const s = data.settings.DRAMA_SPEAK_SCORE_WEIGHT as SettingConfig;
        setSetting(s);
        const parsed = parseFloat(s.value);
        if (!Number.isNaN(parsed)) setWeight(parsed);
      } else {
        showAlert('error', data.error || 'Failed to load gameplay settings');
      }
    } catch {
      showAlert('error', 'Failed to connect to server');
    } finally {
      setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  const save = async () => {
    setSaving(true);
    try {
      const res = await adminFetch(`/admin/api/settings`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ key: 'DRAMA_SPEAK_SCORE_WEIGHT', value: weight.toFixed(1) }),
      });
      const data = await res.json();
      if (data.success) {
        showAlert('success', 'Talk volume saved — takes effect on the next hand');
        await fetchData();
      } else {
        showAlert('error', data.error || 'Failed to save');
      }
    } catch {
      showAlert('error', 'Failed to connect to server');
    } finally {
      setSaving(false);
    }
  };

  const resetToDefault = async () => {
    setSaving(true);
    try {
      const res = await adminFetch(`/admin/api/settings/reset`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ key: 'DRAMA_SPEAK_SCORE_WEIGHT' }),
      });
      const data = await res.json();
      if (data.success) {
        showAlert('success', 'Reset to default');
        await fetchData();
      } else {
        showAlert('error', data.error || 'Failed to reset');
      }
    } catch {
      showAlert('error', 'Failed to connect to server');
    } finally {
      setSaving(false);
    }
  };

  if (loading || !setting) {
    return (
      <div className="admin-loading">
        <div className="admin-loading__spinner" />
        <span className="admin-loading__text">Loading settings...</span>
      </div>
    );
  }

  const savedValue = parseFloat(setting.value);
  const dirty = !Number.isNaN(savedValue) && Math.abs(savedValue - weight) > 1e-6;

  return (
    <div className="us-capture">
      <div className="admin-card">
        <div className="admin-card__header">
          <h3 className="admin-card__title">AI Talk Volume</h3>
          <span
            className={`admin-badge ${setting.is_db_override ? 'admin-badge--primary' : 'admin-badge--warning'}`}
          >
            {setting.is_db_override ? 'Custom' : 'Default'}
          </span>
        </div>
        <p className="admin-card__subtitle">{setting.description}</p>

        <div style={{ margin: '1rem 0' }}>
          <div
            style={{
              display: 'flex',
              justifyContent: 'space-between',
              fontSize: '0.8rem',
              opacity: 0.7,
              marginBottom: '0.25rem',
            }}
          >
            <span>Quieter</span>
            <strong style={{ opacity: 1 }}>{weight.toFixed(1)}</strong>
            <span>Chattier</span>
          </div>
          <input
            type="range"
            className="admin-input"
            min={MIN}
            max={MAX}
            step={STEP}
            value={weight}
            onChange={(e) => setWeight(parseFloat(e.target.value))}
            disabled={saving}
            style={{ width: '100%' }}
          />
          <p className="admin-card__subtitle" style={{ marginTop: '0.5rem' }}>
            {feelLabel(weight)}
          </p>
        </div>

        <p className="admin-card__subtitle">
          Changes take effect on the next hand — no restart. Default is{' '}
          <code>{setting.env_default}</code>.
        </p>
      </div>

      <div className="us-capture__actions">
        <button
          type="button"
          className="admin-btn admin-btn--secondary"
          onClick={resetToDefault}
          disabled={saving || !setting.is_db_override}
        >
          Reset to default
        </button>
        <button
          type="button"
          className="admin-btn admin-btn--primary"
          onClick={save}
          disabled={saving || !dirty}
        >
          {saving ? 'Saving...' : 'Save'}
        </button>
      </div>
    </div>
  );
}
