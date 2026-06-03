import { useState, useEffect, useCallback } from 'react';
import { adminFetch } from '../../utils/api';
import { logger } from '../../utils/logger';
import './SettingsPage.css';

type CoachMode = 'off' | 'reactive' | 'proactive';

const OPTIONS: { value: CoachMode; label: string; desc: string }[] = [
  { value: 'off', label: 'Off', desc: 'No coaching — you play uncoached.' },
  { value: 'reactive', label: 'Ask', desc: 'The coach only chimes in when you ask for a read.' },
  {
    value: 'proactive',
    label: 'Auto',
    desc: 'The coach offers tips on its own as hands play out.',
  },
];

function isMode(v: unknown): v is CoachMode {
  return v === 'off' || v === 'reactive' || v === 'proactive';
}

/**
 * CoachSettings — the default coaching mode for new games (sticky, cross-device).
 *
 * Stored server-side via the profile preference API; new games are stamped with
 * it at creation, so it follows the user across devices. The in-game coach panel
 * still changes the mode live for the current game. We also mirror the value to
 * localStorage('coach_mode') so the next new game's first paint matches before
 * its per-game config loads.
 */
export function CoachSettings() {
  const [mode, setMode] = useState<CoachMode | null>(null); // null = loading
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await adminFetch('/api/profile');
        const data = await res.json();
        if (!cancelled)
          setMode(
            data.success && isMode(data.coach_default_mode) ? data.coach_default_mode : 'off'
          );
      } catch (err) {
        if (!cancelled) {
          logger.warn('[Settings] failed to load coach default', err);
          setMode('off');
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const choose = useCallback(
    async (next: CoachMode) => {
      if (mode === null || saving || next === mode) return;
      const prev = mode;
      setMode(next); // optimistic
      setSaving(true);
      setError(null);
      try {
        const res = await adminFetch('/api/profile/coach-default', {
          method: 'PUT',
          body: JSON.stringify({ mode: next }),
        });
        const data = await res.json();
        if (!data.success) {
          setMode(prev);
          setError(data.error || 'Could not save setting');
          return;
        }
        const stored = isMode(data.coach_default_mode) ? data.coach_default_mode : next;
        setMode(stored);
        try {
          localStorage.setItem('coach_mode', stored);
        } catch {
          /* ignore */
        }
      } catch (err) {
        setMode(prev);
        setError(err instanceof Error ? err.message : 'Could not save setting');
      } finally {
        setSaving(false);
      }
    },
    [mode, saving]
  );

  const active = OPTIONS.find((o) => o.value === mode) ?? OPTIONS[0];
  const disabled = mode === null || saving;

  return (
    <div className="settings-section-body">
      {error && <div className="profile-error">{error}</div>}

      <div className="settings-field">
        <span className="settings-toggle-label">Default coaching mode</span>
        <span className="settings-toggle-desc">
          What new games start with. You can still switch it live during a hand from the coach
          panel.
        </span>
        <div className="settings-segment" role="radiogroup" aria-label="Default coaching mode">
          {OPTIONS.map((o) => (
            <button
              key={o.value}
              type="button"
              role="radio"
              aria-checked={mode === o.value}
              className={`settings-segment-btn ${mode === o.value ? 'is-on' : ''}`}
              disabled={disabled}
              onClick={() => choose(o.value)}
            >
              {o.label}
            </button>
          ))}
        </div>
        <span className="settings-segment-hint">{mode === null ? 'Loading…' : active.desc}</span>
      </div>
    </div>
  );
}

export default CoachSettings;
