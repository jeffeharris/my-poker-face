import { useState, useEffect, useCallback } from 'react';
import { adminFetch } from '../../../../utils/api';
import type { SettingConfig, ShowAlert } from '../types';

interface GameplaySectionProps {
  showAlert: ShowAlert;
}

const MIN = 0.5;
const MAX = 2.5;
const STEP = 0.1;

// The two talk-volume dials, in display order.
const DIALS: { key: string; title: string }[] = [
  { key: 'MIDGAME_SPEAK_WEIGHT', title: 'In-hand chatter' },
  { key: 'DRAMA_SPEAK_SCORE_WEIGHT', title: 'After-hand chatter' },
];

/** Coarse, honest label for a given dial value (not a precise prediction). */
function feelLabel(weight: number): string {
  if (weight <= 0.8) return 'Quiet — AIs mostly only react on the big moments';
  if (weight < 1.15) return 'Reserved';
  if (weight <= 1.45) return 'Balanced (default)';
  if (weight < 1.85) return 'Chatty';
  return 'Very chatty — talk/react on most hands';
}

export function GameplaySection({ showAlert }: GameplaySectionProps) {
  const [settings, setSettings] = useState<Record<string, SettingConfig> | null>(null);
  const [loading, setLoading] = useState(true);
  const [values, setValues] = useState<Record<string, number>>({});
  const [saving, setSaving] = useState<string | null>(null);

  const fetchData = useCallback(async () => {
    try {
      setLoading(true);
      const res = await adminFetch(`/admin/api/settings`);
      const data = await res.json();
      if (data.success) {
        const next: Record<string, SettingConfig> = {};
        const vals: Record<string, number> = {};
        for (const { key } of DIALS) {
          const s = data.settings?.[key] as SettingConfig | undefined;
          if (s) {
            next[key] = s;
            const parsed = parseFloat(s.value);
            vals[key] = Number.isNaN(parsed) ? 1.3 : parsed;
          }
        }
        setSettings(next);
        setValues(vals);
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

  const save = async (key: string) => {
    setSaving(key);
    try {
      const res = await adminFetch(`/admin/api/settings`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ key, value: values[key].toFixed(1) }),
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
      setSaving(null);
    }
  };

  const resetToDefault = async (key: string) => {
    setSaving(key);
    try {
      const res = await adminFetch(`/admin/api/settings/reset`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ key }),
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
      setSaving(null);
    }
  };

  if (loading || !settings) {
    return (
      <div className="admin-loading">
        <div className="admin-loading__spinner" />
        <span className="admin-loading__text">Loading settings...</span>
      </div>
    );
  }

  return (
    <div className="us-capture">
      {DIALS.map(({ key, title }) => {
        const setting = settings[key];
        if (!setting) return null;
        const weight = values[key] ?? 1.3;
        const savedValue = parseFloat(setting.value);
        const dirty = !Number.isNaN(savedValue) && Math.abs(savedValue - weight) > 1e-6;
        const busy = saving === key;
        return (
          <div className="admin-card" key={key}>
            <div className="admin-card__header">
              <h3 className="admin-card__title">{title}</h3>
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
                onChange={(e) => setValues((v) => ({ ...v, [key]: parseFloat(e.target.value) }))}
                disabled={busy}
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

            <div className="us-capture__actions">
              <button
                type="button"
                className="admin-btn admin-btn--secondary"
                onClick={() => resetToDefault(key)}
                disabled={busy || !setting.is_db_override}
              >
                Reset to default
              </button>
              <button
                type="button"
                className="admin-btn admin-btn--primary"
                onClick={() => save(key)}
                disabled={busy || !dirty}
              >
                {busy ? 'Saving...' : 'Save'}
              </button>
            </div>
          </div>
        );
      })}
    </div>
  );
}
