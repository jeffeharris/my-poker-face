import { useCallback, useRef, useState } from 'react';
import { Card } from '../cards';
import { heroCardAnimation } from '../mobile/heroCardAnimation';
// Reuse the real hero-card styles + the heroPresentUp*/heroPullDown* keyframes,
// so tuning MobilePokerTable.css updates this preview live (HMR). This is why the
// gesture here is faithful to the game.
import '../mobile/MobilePokerTable.css';
import './RunoutCommitSandbox.css';

/**
 * Dev-only sandbox to iterate on the all-in run-out hero card-commit gesture
 * (the human "presents" their hole cards over the board, holds, then pulls back
 * when the run-out deals) — without waiting for a random all-in in a real game.
 *
 * Fire the present, the retreat, or the full present→hold→retreat beat, and
 * watch whether the cards reach/cover the board. Drives the same
 * `heroCardAnimation()` + CSS the table uses.
 */

// Sample "dealt" placement so the rest endpoints (the --deal-* vars the keyframes
// start/end at) look natural, mirroring useCardAnimation's messy transforms.
const DEAL = {
  Left: { rotation: -5, offsetX: -10, offsetY: 6 },
  Right: { rotation: 5, offsetX: 10, offsetY: 6 },
} as const;

function heroCardStyle(
  side: 'Left' | 'Right',
  flags: { heroCommitted: boolean; heroRetreating: boolean }
): React.CSSProperties {
  const d = DEAL[side];
  return {
    transform: `rotate(${d.rotation}deg) translateX(${d.offsetX}px) translateY(${d.offsetY}px)`,
    animation: heroCardAnimation(side, { ...flags, isDealing: false }),
    '--deal-rotation': `${d.rotation}deg`,
    '--deal-offset-x': `${d.offsetX}px`,
    '--deal-offset-y': `${d.offsetY}px`,
  } as React.CSSProperties;
}

export function RunoutCommitSandbox() {
  const [committed, setCommitted] = useState(false);
  const [retreating, setRetreating] = useState(false);
  const timers = useRef<number[]>([]);

  const clear = useCallback(() => {
    timers.current.forEach((t) => clearTimeout(t));
    timers.current = [];
  }, []);

  const present = useCallback(() => {
    clear();
    setRetreating(false);
    setCommitted(true);
  }, [clear]);

  const retreat = useCallback(() => setRetreating(true), []);

  const reset = useCallback(() => {
    clear();
    setCommitted(false);
    setRetreating(false);
  }, [clear]);

  // The full beat: present + hold, then retreat ~when the run-out would deal,
  // then settle. Roughly the live cadence (RUNOUT_REVEAL_HOLD ~1.5s, retreat ~0.5s).
  const replay = useCallback(() => {
    reset();
    timers.current.push(window.setTimeout(() => present(), 50));
    timers.current.push(window.setTimeout(() => setRetreating(true), 1700));
    timers.current.push(window.setTimeout(() => reset(), 2600));
  }, [present, reset]);

  const flags = { heroCommitted: committed, heroRetreating: retreating };
  const board: Array<{ rank: string; suit: 'Spades' | 'Hearts' | 'Diamonds' | 'Clubs' }> = [
    { rank: 'A', suit: 'Spades' },
    { rank: 'K', suit: 'Hearts' },
    { rank: '7', suit: 'Diamonds' },
    { rank: '2', suit: 'Clubs' },
    { rank: 'Q', suit: 'Spades' },
  ];

  return (
    <div className="runout-sandbox">
      <div className="runout-sandbox__bar">
        <strong>Run-out commit</strong>
        <span className="runout-sandbox__state">
          {committed ? (retreating ? 'retreating' : 'presenting') : 'rest'}
        </span>
        <span className="runout-sandbox__spacer" />
        <button onClick={present}>Present</button>
        <button onClick={retreat} disabled={!committed || retreating}>
          Retreat
        </button>
        <button onClick={replay}>▶ Replay full beat</button>
        <button onClick={reset}>Reset</button>
      </div>

      <div className="runout-sandbox__felt">
        <div className="runout-sandbox__board">
          {board.map((c, i) => (
            <Card key={i} card={c} faceDown={false} size="large" />
          ))}
        </div>

        <div className={`hero-cards${committed ? ' hero-cards--committed' : ''}`}>
          <div style={heroCardStyle('Left', flags)}>
            <Card card={{ rank: 'A', suit: 'Hearts' }} faceDown={false} size="xlarge" className="hero-card" />
          </div>
          <div style={heroCardStyle('Right', flags)}>
            <Card card={{ rank: 'A', suit: 'Diamonds' }} faceDown={false} size="xlarge" className="hero-card" />
          </div>
        </div>
      </div>
    </div>
  );
}
