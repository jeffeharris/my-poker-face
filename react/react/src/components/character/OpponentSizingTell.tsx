/**
 * OpponentSizingTell — dossier card for Surface B of SIZING_COACH_SURFACES.md.
 *
 * Shows how readable an opponent's bet SIZING is: a size→strength score
 * (do their big bets mean strength?), a verdict (face-up / balanced / reverse),
 * a stability trend (is the tell holding, or are they starting to mix?), and the
 * concrete exploit. Self-fetches GET /api/coach/opponent-tells?opponent=<name>.
 *
 * Renders nothing on error or while loading (dossier sections silently omit, like
 * the rest of the card); shows a quiet "keep playing" note when the sample is too
 * thin to read. The `stability=mixing` warning is the human-facing twin of the
 * bot's Phase B sizing-defense kill switch.
 */

import { useEffect, useState } from 'react';
import { Eye, AlertTriangle } from 'lucide-react';
import { Sparkline } from '../cash/Sparkline';
import type { BankrollPoint } from '../cash/types';
import { config } from '../../config';
import { logger } from '../../utils/logger';
import './OpponentSizingTell.css';

type Verdict = 'face_up' | 'balanced' | 'reverse' | 'unknown';
type Stability = 'stable' | 'mixing' | 'insufficient';

interface SizingTellEntry {
  axis: 'sizing';
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
  exploit: string | null;
  trend: { series: (number | null)[] };
}
interface TellsResponse {
  opponent: string;
  face_up_threshold: number;
  confirm_min_bets: number;
  tells: SizingTellEntry[];
  message?: string;
}

const VERDICT_TONE: Record<Verdict, 'up' | 'down' | 'flat'> = {
  // "up" tone = exploitable signal for you (face-up). reverse = caution (they
  // bluff big). balanced/unknown = neutral.
  face_up: 'up',
  reverse: 'down',
  balanced: 'flat',
  unknown: 'flat',
};
const VERDICT_PILL: Record<Verdict, string> = {
  face_up: 'FACE-UP',
  reverse: 'BLUFFS BIG',
  balanced: 'BALANCED',
  unknown: '—',
};

export function OpponentSizingTell({ opponent }: { opponent: string }) {
  const [data, setData] = useState<TellsResponse | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    if (!opponent) {
      setLoading(false);
      return;
    }
    setLoading(true);
    const qs = `?opponent=${encodeURIComponent(opponent)}`;
    fetch(`${config.API_URL}/api/coach/opponent-tells${qs}`, { credentials: 'include' })
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`tells ${r.status}`))))
      .then((json: TellsResponse) => {
        if (!cancelled) setData(json);
      })
      .catch((err) => {
        logger.error('Failed to load opponent sizing tell:', err);
        if (!cancelled) setData(null);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [opponent]);

  // Silently omit while loading or on error — matches the dossier's other
  // optional sections (they appear only when there's something to show).
  if (loading || !data) return null;

  const tell = data.tells[0];

  // Thin sample → a quiet "keep playing" note instead of a fake number.
  if (!tell) {
    if (!data.message) return null;
    return (
      <div className="sizing-tell sizing-tell--empty">
        <div className="sizing-tell__head">
          <Eye size={13} aria-hidden="true" />
          <span className="sizing-tell__title">Sizing tell</span>
        </div>
        <p className="sizing-tell__empty-note">{data.message}</p>
      </div>
    );
  }

  const points: BankrollPoint[] = tell.trend.series
    .map((v, i) => (v == null ? null : { t: `#${i + 1}`, value: v }))
    .filter((p): p is BankrollPoint => p !== null);

  return (
    <div className={`sizing-tell sizing-tell--${tell.verdict}`}>
      <div className="sizing-tell__head">
        <Eye size={13} aria-hidden="true" />
        <span className="sizing-tell__title">{tell.label}</span>
        <span className={`sizing-tell__pill sizing-tell__pill--${tell.verdict}`}>
          {VERDICT_PILL[tell.verdict]}
        </span>
        <span className="sizing-tell__meta">
          {tell.n_bets} bets · {tell.confidence === 'high' ? 'confident' : 'watching'}
        </span>
      </div>

      <div className="sizing-tell__readout">
        {points.length >= 2 && (
          <Sparkline
            points={points}
            tone={VERDICT_TONE[tell.verdict]}
            width={120}
            height={28}
            className="sizing-tell__spark"
            label={`${opponent} sizing tell over time`}
          />
        )}
        <span className="sizing-tell__score">
          score {tell.score >= 0 ? '+' : ''}
          {tell.score.toFixed(2)}
          <span className="sizing-tell__score-ref">
            {' '}
            (face-up ≥ {data.face_up_threshold.toFixed(2)})
          </span>
        </span>
      </div>

      {tell.exploit && <p className="sizing-tell__exploit">→ {tell.exploit}</p>}

      {tell.stability === 'mixing' && (
        <p className="sizing-tell__warn">
          <AlertTriangle size={12} aria-hidden="true" />
          They&rsquo;re starting to mix their sizing — the read is going stale, ease off.
        </p>
      )}
    </div>
  );
}
