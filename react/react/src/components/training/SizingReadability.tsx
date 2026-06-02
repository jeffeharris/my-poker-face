/**
 * SizingReadability — Surface A of SIZING_COACH_SURFACES.md.
 *
 * Self-coaching twin of the opponent dossier's sizing tell: how readable YOUR OWN
 * bet sizing is, over time. If your big bets always mean strength, observant
 * opponents fold for free — a leak to fix by mixing in big bluffs. Self-fetches
 * GET /api/coach/sizing-readability and reuses the dossier card's `.sizing-tell`
 * styling. Unlike the dossier card, on this dedicated coach page it shows a
 * "keep playing" note when the sample is thin (so the player knows it exists).
 */

import { useEffect, useState } from 'react';
import { Ruler, AlertTriangle } from 'lucide-react';
import { Sparkline } from '../cash/Sparkline';
import type { BankrollPoint } from '../cash/types';
import { config } from '../../config';
import { logger } from '../../utils/logger';
import '../character/OpponentSizingTell.css';

type Verdict = 'face_up' | 'balanced' | 'reverse' | 'unknown';
type Stability = 'stable' | 'mixing' | 'insufficient';

interface Readability {
  label: string;
  verdict: Verdict;
  score: number;
  big_eq: number | null;
  small_eq: number | null;
  confidence: 'low' | 'high';
  stability: Stability;
  n_bets: number;
  n_big: number;
  n_small: number;
  advice: string | null;
  trend: { series: (number | null)[] };
}
interface Response {
  face_up_threshold: number;
  confirm_min_bets: number;
  readability: Readability | null;
  message?: string;
}

const VERDICT_TONE: Record<Verdict, 'up' | 'down' | 'flat'> = {
  // face-up is a LEAK for you → "down" (ruby caution). balanced is clean → flat.
  face_up: 'down',
  reverse: 'down',
  balanced: 'flat',
  unknown: 'flat',
};
const VERDICT_PILL: Record<Verdict, string> = {
  face_up: 'FACE-UP',
  reverse: 'WEAK BIG',
  balanced: 'BALANCED',
  unknown: '—',
};

export function SizingReadability() {
  const [data, setData] = useState<Response | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fetch(`${config.API_URL}/api/coach/sizing-readability`, { credentials: 'include' })
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`readability ${r.status}`))))
      .then((json: Response) => {
        if (!cancelled) setData(json);
      })
      .catch((err) => {
        logger.error('Failed to load sizing readability:', err);
        if (!cancelled) setData(null);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (loading || !data) return null;

  const r = data.readability;

  if (!r) {
    return (
      <div className="sizing-tell sizing-tell--empty">
        <div className="sizing-tell__head">
          <Ruler size={13} aria-hidden="true" />
          <span className="sizing-tell__title">Your sizing tells</span>
        </div>
        <p className="sizing-tell__empty-note">
          {data.message ?? 'Not enough big bets yet to read your sizing — keep playing.'}
        </p>
      </div>
    );
  }

  const points: BankrollPoint[] = r.trend.series
    .map((v, i) => (v == null ? null : { t: `#${i + 1}`, value: v }))
    .filter((p): p is BankrollPoint => p !== null);

  // The leak is a CLEAN read when balanced — flip the verdict styling so
  // "balanced" reads as the good state (no caution rail).
  const verdictClass = r.verdict === 'balanced' ? 'balanced' : r.verdict;

  return (
    <div className={`sizing-tell sizing-tell--${verdictClass}`}>
      <div className="sizing-tell__head">
        <Ruler size={13} aria-hidden="true" />
        <span className="sizing-tell__title">{r.label}</span>
        <span className={`sizing-tell__pill sizing-tell__pill--${r.verdict}`}>
          {VERDICT_PILL[r.verdict]}
        </span>
        <span className="sizing-tell__meta">
          {r.n_bets} bets · {r.confidence === 'high' ? 'confident' : 'watching'}
        </span>
      </div>

      <div className="sizing-tell__readout">
        {points.length >= 2 && (
          <Sparkline
            points={points}
            tone={VERDICT_TONE[r.verdict]}
            width={120}
            height={28}
            className="sizing-tell__spark"
            label="Your sizing readability over time"
          />
        )}
        <span className="sizing-tell__score">
          score {r.score >= 0 ? '+' : ''}
          {r.score.toFixed(2)}
          <span className="sizing-tell__score-ref">
            {' '}
            (face-up ≥ {data.face_up_threshold.toFixed(2)})
          </span>
        </span>
      </div>

      {r.advice && <p className="sizing-tell__exploit">→ {r.advice}</p>}
      {r.verdict === 'balanced' && (
        <p className="sizing-tell__exploit">✓ Opponents can&rsquo;t read your hand from your bet size.</p>
      )}

      {r.stability === 'mixing' && r.verdict === 'face_up' && (
        <p className="sizing-tell__warn">
          <AlertTriangle size={12} aria-hidden="true" />
          You&rsquo;re starting to balance your big bets — keep it up.
        </p>
      )}
    </div>
  );
}
