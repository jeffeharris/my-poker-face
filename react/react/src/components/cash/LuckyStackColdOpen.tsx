/**
 * LuckyStackColdOpen — the cinematic cold open before intake (The Circuit).
 *
 * Two beats, tap-to-advance, before the Lucky Stack intake modal:
 *   1. BLACK — a short punchy setup typed onto black (rain, a wrong turn, you
 *      only wanted coffee). Sets the silent-protagonist / chill-absurd tone.
 *   2. DINER — the neon establishing shot (`/lucky-stack.jpg`) lands as a reveal;
 *      the photo's own signage ("POKER ROOM — good hands served daily") does the
 *      scene-setting, so the overlay stays to one wry line.
 * `onDone` hands off to `<LuckyStackIntake>` (the waitress + name + backstory).
 *
 * Shown once at the front of a brand-new career player's first lobby load
 * (gated by the same `intake_needed` as the intake). Skippable. Client-side
 * only — a refresh mid-cold-open just replays it (intake_needed is still true),
 * which is cheap and fine.
 *
 * Portaled to body so it covers the page chrome.
 */

import { useState } from 'react';
import { createPortal } from 'react-dom';
import { DramaticReserve } from '../shared/DramaticText';
import './LuckyStackColdOpen.css';

// Black-screen setup, typed as print-style beats (one sentence per line).
const COLD_OPEN_BEATS = [
  'Late. Raining. A road you don’t quite remember turning onto.',
  'Then — light up ahead. Neon, humming through the wet.',
  'You only wanted coffee. Maybe a slice of pie.',
].join('\n');

// One wry line over the diner; the neon in the photo says the rest.
const DINER_BEAT = 'Huh. “Poker Room.” …You’re pretty sure you came in for the biscuits and gravy.';

interface LuckyStackColdOpenProps {
  onDone: () => void;
}

export function LuckyStackColdOpen({ onDone }: LuckyStackColdOpenProps) {
  const [phase, setPhase] = useState<'black' | 'diner'>('black');

  const advance = () => {
    if (phase === 'black') setPhase('diner');
    else onDone();
  };

  return createPortal(
    <div
      className={`coldopen coldopen--${phase}`}
      onClick={advance}
      role="button"
      aria-label="Continue"
    >
      {phase === 'diner' && (
        <img
          className="coldopen__bg"
          src="/lucky-stack.jpg"
          alt="The Lucky Stack — a neon 50s diner and poker room at night"
        />
      )}
      <div className="coldopen__scrim" aria-hidden="true" />
      <div className="coldopen__text">
        <DramaticReserve key={phase} text={phase === 'black' ? COLD_OPEN_BEATS : DINER_BEAT} />
      </div>
      <button
        className="coldopen__skip"
        type="button"
        onClick={(e) => {
          e.stopPropagation();
          onDone();
        }}
      >
        Skip
      </button>
      <div className="coldopen__hint">
        {phase === 'black' ? 'tap to continue' : 'tap to head inside'}
      </div>
    </div>,
    document.body
  );
}
