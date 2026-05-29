import { useEffect, useState, useCallback } from 'react';
import { adminFetch } from '../../utils/api';
import { logger } from '../../utils/logger';
import './SettingsPage.css';

type GameSpeed = 'standard' | 'after_fold' | 'always';

const OPTIONS: { value: GameSpeed; label: string; desc: string }[] = [
  { value: 'standard', label: 'Standard', desc: 'Full AI deliberation on every turn.' },
  {
    value: 'after_fold',
    label: 'After I fold',
    desc: 'Once you fold, the rest of the hand resolves fast so the next deal comes quickly.',
  },
  {
    value: 'always',
    label: 'Always',
    desc: 'Every AI turn resolves fast. Fastest play, but you skip the AIs’ turn-by-turn table talk.',
  },
];

function isSpeed(v: unknown): v is GameSpeed {
  return v === 'standard' || v === 'after_fold' || v === 'always';
}

/**
 * GameplaySettings — sticky per-user game-speed preference.
 *
 * Standard / After I fold / Always. "Fast" turns use no-LLM tiered controllers
 * (the same path the in-game fast-forward button uses), so they resolve in
 * sub-100ms at the cost of the AIs' table talk. Server-backed (cross-device).
 */
export function GameplaySettings() {
  const [speed, setSpeed] = useState<GameSpeed | null>(null); // null = loading
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await adminFetch('/api/profile');
        const data = await res.json();
        if (!cancelled) setSpeed(data.success && isSpeed(data.game_speed) ? data.game_speed : 'standard');
      } catch (err) {
        if (!cancelled) {
          logger.warn('[Settings] failed to load game speed', err);
          setSpeed('standard');
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const choose = useCallback(
    async (next: GameSpeed) => {
      if (speed === null || saving || next === speed) return;
      const prev = speed;
      setSpeed(next); // optimistic
      setSaving(true);
      setError(null);
      try {
        const res = await adminFetch('/api/profile/game-speed', {
          method: 'PUT',
          body: JSON.stringify({ speed: next }),
        });
        const data = await res.json();
        if (!data.success) {
          setSpeed(prev);
          setError(data.error || 'Could not save setting');
          return;
        }
        setSpeed(isSpeed(data.game_speed) ? data.game_speed : next);
      } catch (err) {
        setSpeed(prev);
        setError(err instanceof Error ? err.message : 'Could not save setting');
      } finally {
        setSaving(false);
      }
    },
    [speed, saving]
  );

  const active = OPTIONS.find((o) => o.value === speed) ?? OPTIONS[0];
  const disabled = speed === null || saving;

  return (
    <div className="settings-section-body">
      {error && <div className="profile-error">{error}</div>}

      <div className="settings-field">
        <span className="settings-toggle-label">Game speed</span>
        <span className="settings-toggle-desc">
          How fast hands resolve when it isn’t your turn.
        </span>
        <div className="settings-segment" role="radiogroup" aria-label="Game speed">
          {OPTIONS.map((o) => (
            <button
              key={o.value}
              type="button"
              role="radio"
              aria-checked={speed === o.value}
              className={`settings-segment-btn ${speed === o.value ? 'is-on' : ''}`}
              disabled={disabled}
              onClick={() => choose(o.value)}
            >
              {o.label}
            </button>
          ))}
        </div>
        <span className="settings-segment-hint">{speed === null ? 'Loading…' : active.desc}</span>
      </div>
    </div>
  );
}

export default GameplaySettings;
