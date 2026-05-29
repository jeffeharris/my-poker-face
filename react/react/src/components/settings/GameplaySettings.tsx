import { useEffect, useState, useCallback } from 'react';
import { adminFetch } from '../../utils/api';
import { logger } from '../../utils/logger';
import './SettingsPage.css';

/**
 * GameplaySettings — sticky per-user gameplay preferences.
 *
 * First setting: "Speed through the hand after I fold" (auto_fast_fold). When
 * on, folding fast-forwards the rest of the orbit (no-LLM AI decisions) so the
 * next hand arrives quickly — at the cost of the AIs' turn-by-turn table talk
 * for that hand. Reads/writes the profile preference API.
 */
export function GameplaySettings() {
  const [autoFastFold, setAutoFastFold] = useState<boolean | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await adminFetch('/api/profile');
        const data = await res.json();
        if (!cancelled && data.success) setAutoFastFold(!!data.auto_fast_fold);
      } catch (err) {
        if (!cancelled) {
          logger.warn('[Settings] failed to load gameplay prefs', err);
          setAutoFastFold(false);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const toggleAutoFastFold = useCallback(async () => {
    if (autoFastFold === null || saving) return;
    const next = !autoFastFold;
    setAutoFastFold(next); // optimistic
    setSaving(true);
    setError(null);
    try {
      const res = await adminFetch('/api/profile/auto-fast-fold', {
        method: 'PUT',
        body: JSON.stringify({ enabled: next }),
      });
      const data = await res.json();
      if (!data.success) {
        setAutoFastFold(!next); // revert
        setError(data.error || 'Could not save setting');
      } else {
        setAutoFastFold(!!data.auto_fast_fold);
      }
    } catch (err) {
      setAutoFastFold(!next); // revert
      setError(err instanceof Error ? err.message : 'Could not save setting');
    } finally {
      setSaving(false);
    }
  }, [autoFastFold, saving]);

  return (
    <div className="settings-section-body">
      {error && <div className="profile-error">{error}</div>}

      <div className="settings-toggle-row">
        <div className="settings-toggle-text">
          <span className="settings-toggle-label">Speed through the hand after I fold</span>
          <span className="settings-toggle-desc">
            Once you fold, the remaining players finish the hand fast so the next deal arrives
            quickly. You'll skip their turn-by-turn table talk for that hand.
          </span>
        </div>
        <button
          type="button"
          role="switch"
          aria-checked={autoFastFold === true}
          aria-label="Speed through the hand after I fold"
          className={`settings-switch ${autoFastFold ? 'is-on' : ''}`}
          disabled={autoFastFold === null || saving}
          onClick={toggleAutoFastFold}
        >
          <span className="settings-switch-knob" />
        </button>
      </div>
    </div>
  );
}

export default GameplaySettings;
