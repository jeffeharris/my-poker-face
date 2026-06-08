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

// Black-screen setup, typed as print-style beats — one SHORT line each so they
// land staccato with a pause between (DramaticReserve pauses per line). Sets the
// arrival — lost, rainy, late — and hands the poker reveal to the waitress (her
// first intake line is the payoff), so the cold open doesn't step on its own joke.
const COLD_OPEN_BEATS = [
  'Late.',
  'Raining.',
  'You’re a long way from anywhere.',
  'Then — a glow up ahead.',
  'Warm neon, humming through the wet.',
].join('\n');

// Under the diner, staccato: the irony (coffee vs. the POKER ROOM blazing in the
// photo) sets up the waitress without naming the joke.
const DINER_BEAT = [
  'Coffee.',
  'A booth.',
  'A minute out of the rain.',
  'That’s the whole plan.',
].join('\n');

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
      {/* key on phase so the scrim + text remount and re-fade on the black→diner
          change, softening the cut. */}
      <div className="coldopen__scrim" aria-hidden="true" key={`scrim-${phase}`} />
      <div className="coldopen__text" key={`text-${phase}`}>
        <DramaticReserve text={phase === 'black' ? COLD_OPEN_BEATS : DINER_BEAT} />
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
