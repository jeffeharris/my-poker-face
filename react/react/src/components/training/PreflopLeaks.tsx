import { useEffect, useState } from 'react';
import { TrendingUp } from 'lucide-react';
import { PageLayout, PageHeader, MenuBar, BackButton } from '../shared';
import { config } from '../../config';
import { logger } from '../../utils/logger';
import './PreflopLeaks.css';

interface PositionRow {
  position: string; // early | middle | late | blind
  decisions: number;
  vpip_pct: number;
  reference_vpip_pct: number;
  loose_plays: number;
}
interface Leak {
  position: string;
  hand: string;
  times_played: number;
  times_seen: number;
  vpip_pct: number;
}
interface LeaksResponse {
  total_decisions: number;
  enough_data: boolean;
  min_for_signal: number;
  by_position: PositionRow[];
  leaks: Leak[];
}

const POSITION_LABEL: Record<string, string> = {
  early: 'Early (UTG / MP)',
  middle: 'Middle (HJ)',
  late: 'Late (CO / BTN)',
  blind: 'Blinds (SB / BB)',
};

interface PreflopLeaksProps {
  onBack: () => void;
}

export function PreflopLeaks({ onBack }: PreflopLeaksProps) {
  const [data, setData] = useState<LeaksResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<string | null>(null);
  const [feedbackLoading, setFeedbackLoading] = useState(false);

  const askCoach = async () => {
    if (feedbackLoading) return;
    setFeedbackLoading(true);
    try {
      const resp = await fetch(`${config.API_URL}/api/coach/preflop-leaks/feedback`, {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
      });
      const json = await resp.json();
      setFeedback(resp.ok ? json.feedback : (json.error ?? 'The coach is unavailable right now.'));
    } catch (err) {
      logger.error('Failed to get coach feedback:', err);
      setFeedback('The coach is unavailable right now.');
    } finally {
      setFeedbackLoading(false);
    }
  };

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const resp = await fetch(`${config.API_URL}/api/coach/preflop-leaks`, {
          credentials: 'include',
        });
        if (!resp.ok) throw new Error(`Leaks returned ${resp.status}`);
        const json = await resp.json();
        if (!cancelled) setData(json);
      } catch (err) {
        logger.error('Failed to load preflop leaks:', err);
        if (!cancelled) setError('Could not load your preflop review.');
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <>
      <MenuBar showUserInfo />
      <PageLayout variant="top" glowColor="emerald" maxWidth="md" hasMenuBar>
        <BackButton onClick={onBack} />
        <PageHeader
          title="Your Preflop Game"
          subtitle="What you actually play, from your real hands"
          titleVariant="primary"
        />

        {loading && <div className="pfl-state">Reviewing your hands…</div>}
        {error && <div className="pfl-state pfl-error">{error}</div>}

        {data && !data.enough_data && (
          <div className="pfl-state">
            <TrendingUp size={28} />
            <p>
              Play a bit more and your preflop tendencies will show up here.
              <br />
              <strong>
                {data.total_decisions} / {data.min_for_signal}
              </strong>{' '}
              decisions analyzed so far.
            </p>
          </div>
        )}

        {data && data.enough_data && (
          <div className="pfl-body">
            <p className="pfl-intro">
              Across <strong>{data.total_decisions}</strong> preflop decisions. Your VPIP
              (how often you play a hand) is shown next to a standard opening range for
              orientation — it's context, not a grade (your number includes calls and
              blind defense).
            </p>

            <div className="pfl-positions">
              {data.by_position.map((row) => (
                <div key={row.position} className="pfl-pos">
                  <span className="pfl-pos-name">{POSITION_LABEL[row.position] ?? row.position}</span>
                  <span className="pfl-pos-bar-wrap">
                    <span className="pfl-pos-bar" style={{ width: `${Math.min(100, row.vpip_pct)}%` }} />
                    <span
                      className="pfl-pos-ref"
                      style={{ left: `${Math.min(100, row.reference_vpip_pct)}%` }}
                      title={`standard opens ~${row.reference_vpip_pct}%`}
                    />
                  </span>
                  <span className="pfl-pos-vpip">
                    {row.vpip_pct}%
                    <span className="pfl-pos-ref-label">std ~{row.reference_vpip_pct}%</span>
                  </span>
                  <span className="pfl-pos-n">{row.decisions} hands</span>
                </div>
              ))}
            </div>

            <h3 className="pfl-leaks-head">Hands you keep playing that are below your range</h3>
            {data.leaks.length === 0 ? (
              <p className="pfl-clean">
                Nothing jumps out — you're not habitually playing trash. Nice discipline.
              </p>
            ) : (
              <ul className="pfl-leaks">
                {data.leaks.map((lk) => (
                  <li key={`${lk.position}-${lk.hand}`} className="pfl-leak">
                    <span className="pfl-leak-hand">{lk.hand}</span>
                    <span className="pfl-leak-detail">
                      played <strong>{lk.times_played}</strong> of {lk.times_seen} times from{' '}
                      {POSITION_LABEL[lk.position] ?? lk.position}
                    </span>
                  </li>
                ))}
              </ul>
            )}
            <p className="pfl-note">
              Compared to a standard tight-aggressive opening range. We only flag hands you
              voluntarily play that sit below your position's range — folding tight isn't
              counted (it can be the right play facing a raise).
            </p>

            <button
              type="button"
              className="pfl-ask"
              onClick={askCoach}
              disabled={feedbackLoading}
            >
              {feedbackLoading ? 'Coach is reviewing…' : 'Ask the coach about this'}
            </button>
            {feedback && (
              <div className="pfl-feedback">
                <span className="pfl-feedback-label">Coach</span>
                <p>{feedback}</p>
              </div>
            )}
          </div>
        )}
      </PageLayout>
    </>
  );
}
