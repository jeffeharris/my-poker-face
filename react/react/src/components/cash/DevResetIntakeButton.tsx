/**
 * DevResetIntakeButton — a playtest-only floating button that wipes the current
 * user's career back to the Lucky Stack intake and drops them at the cold open.
 *
 * TEMPORARY testing affordance for iterating on the Circuit intro. Self-gates on
 * `config.ENABLE_DEBUG` (VITE_ENABLE_DEBUG=true, dev only) AND the server route
 * 404s outside dev — double-gated so it can never fire for a real player. Mounted
 * globally in App so it's reachable from the menu, lobby, or a table mid-test.
 *
 * On success it hard-reloads into /cash: the reset cleared `intake_complete`, so
 * the lobby fetch returns `intake_needed` and the cold open replays from black.
 */

import { useState } from 'react';
import { config } from '../../config';
import { devResetIntake } from './api';

export function DevResetIntakeButton() {
  const [busy, setBusy] = useState(false);
  if (!config.ENABLE_DEBUG) return null;

  const reset = async () => {
    if (busy) return;
    if (!window.confirm('DEV: wipe your career and replay the Lucky Stack intake?')) return;
    setBusy(true);
    try {
      await devResetIntake();
      // Hard reload into /cash so App's intake gate + the lobby fetch re-run from
      // a clean slate and the cold open plays from the top.
      window.location.href = '/cash';
    } catch (e) {
      setBusy(false);
      window.alert(`Reset failed: ${e instanceof Error ? e.message : String(e)}`);
    }
  };

  return (
    <button
      type="button"
      onClick={reset}
      disabled={busy}
      title="DEV: reset career & replay the Lucky Stack intake"
      style={{
        position: 'fixed',
        left: 10,
        bottom: 10,
        zIndex: 2147483647,
        padding: '6px 10px',
        fontSize: 11,
        fontWeight: 700,
        letterSpacing: '0.04em',
        color: '#ffd98a',
        background: 'rgba(20, 12, 6, 0.82)',
        border: '1px solid #8a6334',
        borderRadius: 7,
        cursor: busy ? 'default' : 'pointer',
        opacity: busy ? 0.6 : 0.9,
        backdropFilter: 'blur(2px)',
      }}
    >
      {busy ? '↻ resetting…' : '↻ reset intake'}
    </button>
  );
}
