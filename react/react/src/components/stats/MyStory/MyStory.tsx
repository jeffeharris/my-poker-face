/**
 * MyStory — the player's CIRCUIT (cash-mode) career story. Each cash session
 * produces one narrative beat; the session beats combine into the arc. Reads
 * GET /api/journey (deterministic facts over hand_history); "Narrate" re-fetches
 * ?voiced=1 for the LLM-narrated session beats + arc, grounded in those facts.
 */
import { useCallback, useEffect, useState } from 'react';

import { config } from '../../../config';
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

interface Session {
  game_id: string;
  summary: string;
  stats: SessionStats;
  beats: Beat[];
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
}

interface MyStoryProps {
  onBack: () => void;
}

const fmt = (n: number) => `${n > 0 ? '+' : ''}${n.toLocaleString()}`;

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
    <div className="mystory">
      <div className="mystory__bar">
        <button className="mystory__back" onClick={onBack}>
          ← Back
        </button>
        <h1 className="mystory__title">Your Circuit</h1>
        {hasStory && (
          <button className="mystory__narrate" disabled={narrating} onClick={() => load(true)}>
            {narrating ? 'Narrating…' : voiced ? '↻ Re-narrate' : '✨ Narrate my story'}
          </button>
        )}
      </div>

      {loading && <p className="mystory__muted">Reading your hands…</p>}
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
        </div>
      )}

      {!loading &&
        journey?.sessions?.map((s) => (
          <div className="mystory__session" key={s.game_id}>
            <div className="mystory__session-head">
              <span className="mystory__kind">Session</span>
              {s.stats.net_chips === null ? (
                <span className="mystory__net mystory__muted">in progress</span>
              ) : (
                <span className={`mystory__net ${s.stats.net_chips >= 0 ? 'up' : 'down'}`}>
                  {fmt(s.stats.net_chips)}
                </span>
              )}
            </div>
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
  );
}
