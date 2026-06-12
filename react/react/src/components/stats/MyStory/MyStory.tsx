/**
 * MyStory — the player's CIRCUIT (cash-mode) career story. Each cash session
 * produces one narrative beat; the session beats combine into the arc. Reads
 * GET /api/journey (deterministic facts over hand_history); "Narrate" re-fetches
 * ?voiced=1 for the LLM-narrated session beats + arc, grounded in those facts.
 */
import { useCallback, useEffect, useState } from 'react';

import { config } from '../../../config';
import { Sparkline } from '../../cash/Sparkline';
import type { BankrollPoint } from '../../cash/types';
import { MenuBar } from '../../shared/MenuBar';
import { PageLayout } from '../../shared/PageLayout';
import { Skeleton } from '../../shared/Skeleton';
import './MyStory.css';

interface Beat {
  hand_number: number;
  text: string;
  headline?: string; // why this hand mattered (pot size, swing, cooler, all-in…)
  score?: number; // 0-100 drama score (hands are surfaced ranked by this)
}

interface SessionStats {
  hands_played: number;
  hands_won: number;
  net_chips: number | null; // null while the session is in progress (no ledger result)
  biggest_pot_won: number;
  buy_in: number;
  take_home: number | null;
  in_progress: boolean;
}

interface Preflop {
  hands: number;
  vpip_pct: number; // % of hands played voluntarily preflop
  pfr_pct: number; // % of hands raised preflop
  premium: number; // count of premium starting hands (AA/KK/QQ/JJ/AKs)
  avg_hand_pct: number | null; // avg starting-hand quality as a top-X% (lower = stronger)
}

interface Session {
  game_id: string;
  summary: string;
  stats: SessionStats;
  beats: Beat[];
  stack_curve: BankrollPoint[]; // chip stack after each hand of the session
  stake_label: string | null; // e.g. "$200"
  table_name: string | null; // the named room, e.g. "Hotel Mezzanine"
  started_at: string | null; // ISO timestamp the session began
  preflop: Preflop | null; // VPIP / PFR / starting-hand quality
  beat?: string; // the voiced session beat (present only when ?voiced=1)
}

interface Arc {
  sessions: number;
  ended_sessions: number;
  winning_sessions: number;
  total_hands: number;
  total_hands_won: number;
  total_net_chips: number;
  biggest_pot: number;
}

interface Journey {
  player: string | null;
  sessions: Session[];
  arc: Arc | null;
  arc_beat: string | null;
  preflop_overall: Preflop | null;
}

interface MyStoryProps {
  onBack: () => void;
}

// Unicode minus for negatives (matches Sparkline + CareerHighlightsCard).
const fmt = (n: number) => `${n > 0 ? '+' : n < 0 ? '−' : ''}${Math.abs(n).toLocaleString()}`;

const fmtDate = (iso: string | null): string => {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
};

function PreflopStats({ p }: { p: Preflop }) {
  return (
    <div className="mystory__preflop">
      <span className="mystory__stat">
        <b>{p.vpip_pct}%</b> VPIP
      </span>
      <span className="mystory__stat">
        <b>{p.pfr_pct}%</b> PFR
      </span>
      {p.avg_hand_pct != null && (
        <span className="mystory__stat">
          avg hand <b>top {p.avg_hand_pct}%</b>
        </span>
      )}
      {p.premium > 0 && (
        <span className="mystory__stat">
          <b>{p.premium}</b> premium
        </span>
      )}
    </div>
  );
}

export function MyStory({ onBack }: MyStoryProps) {
  const [journey, setJourney] = useState<Journey | null>(null);
  const [loading, setLoading] = useState(true);
  const [narrating, setNarrating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (voiced: boolean) => {
    if (voiced) setNarrating(true);
    else setLoading(true);
    setError(null);
    try {
      const url = `${config.API_URL}/api/journey${voiced ? '?voiced=1' : ''}`;
      const res = await fetch(url, { credentials: 'include' });
      if (!res.ok) throw new Error(`Failed to load your story (${res.status})`);
      setJourney((await res.json()) as Journey);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not load your story.');
    } finally {
      setLoading(false);
      setNarrating(false);
    }
  }, []);

  useEffect(() => {
    load(false);
  }, [load]);

  const hasStory = !!journey?.sessions?.length;
  const voiced = !!journey?.arc_beat;

  return (
    <>
      <MenuBar onBack={onBack} title="Your Circuit" showUserInfo onMainMenu={onBack} />
      <PageLayout variant="top" glowColor="gold" maxWidth="md" hasMenuBar>
        <div className="mystory">
          {hasStory && (
            <div className="mystory__actions">
              <button className="mystory__narrate" disabled={narrating} onClick={() => load(true)}>
                {narrating ? 'Narrating…' : voiced ? '↻ Re-narrate' : '✨ Narrate my story'}
              </button>
            </div>
          )}

          {loading && (
            <div aria-busy="true">
              <div className="mystory__arc">
                <Skeleton width="40%" height="1.3rem" style={{ marginBottom: 'var(--space-3)' }} />
                <Skeleton width="100%" height="0.9rem" style={{ marginBottom: 'var(--space-2)' }} />
                <Skeleton width="82%" height="0.9rem" />
              </div>
              {[0, 1].map((i) => (
                <div className="mystory__session" key={i}>
                  <Skeleton width="30%" height="1rem" style={{ marginBottom: 'var(--space-3)' }} />
                  <Skeleton
                    width="100%"
                    height="0.85rem"
                    style={{ marginBottom: 'var(--space-2)' }}
                  />
                  <Skeleton width="68%" height="0.85rem" />
                </div>
              ))}
            </div>
          )}
          {error && <p className="mystory__error">{error}</p>}
          {!loading && !error && !hasStory && (
            <p className="mystory__muted">
              No circuit story yet — sit at a cash table and your career will write itself.
            </p>
          )}

          {!loading && journey?.arc && (
            <div className="mystory__arc">
              <h2>The Arc So Far</h2>
              {journey.arc_beat ? (
                <p>{journey.arc_beat}</p>
              ) : (
                <p>
                  {journey.arc.sessions} session{journey.arc.sessions !== 1 ? 's' : ''},{' '}
                  {journey.arc.winning_sessions} winning · {journey.arc.total_hands} hands,{' '}
                  {journey.arc.total_hands_won} won · net{' '}
                  <span className={journey.arc.total_net_chips >= 0 ? 'up' : 'down'}>
                    {fmt(journey.arc.total_net_chips)}
                  </span>{' '}
                  chips · biggest pot {journey.arc.biggest_pot.toLocaleString()}
                </p>
              )}
              {journey.preflop_overall && <PreflopStats p={journey.preflop_overall} />}
            </div>
          )}

          {!loading &&
            journey?.sessions?.map((s) => (
              <div className="mystory__session" key={s.game_id}>
                <div className="mystory__session-head">
                  <div className="mystory__session-id">
                    <span className="mystory__table">{s.table_name || 'Cash table'}</span>
                    <span className="mystory__meta">
                      {s.stake_label && <span className="mystory__stake">{s.stake_label}</span>}
                      {s.started_at && (
                        <span className="mystory__date">{fmtDate(s.started_at)}</span>
                      )}
                    </span>
                  </div>
                  {s.stats.net_chips === null ? (
                    <span className="mystory__net mystory__muted">in progress</span>
                  ) : (
                    <span className={`mystory__net ${s.stats.net_chips >= 0 ? 'up' : 'down'}`}>
                      {fmt(s.stats.net_chips)}
                    </span>
                  )}
                </div>
                {s.stack_curve.length >= 2 && (
                  <Sparkline
                    className="mystory__spark"
                    points={s.stack_curve}
                    tone={
                      s.stats.net_chips == null
                        ? 'flat'
                        : s.stats.net_chips > 0
                          ? 'up'
                          : s.stats.net_chips < 0
                            ? 'down'
                            : 'flat'
                    }
                    label="Chip stack across the session"
                  />
                )}
                {s.preflop && <PreflopStats p={s.preflop} />}
                <p className="mystory__summary">{s.beat || s.summary}</p>
                {s.beats.length > 0 && (
                  <ul className="mystory__beats">
                    {s.beats.map((b) => (
                      <li key={b.hand_number}>
                        <span className="mystory__hand">#{b.hand_number}</span> {b.text}
                        {b.headline && <span className="mystory__headline">{b.headline}</span>}
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            ))}
        </div>
      </PageLayout>
    </>
  );
}
