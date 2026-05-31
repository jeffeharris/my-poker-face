import { useCallback, useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { Target } from 'lucide-react';
import { PageLayout, PageHeader, MenuBar, BackButton } from '../shared';
import { config } from '../../config';
import { logger } from '../../utils/logger';
import './PreflopDrill.css';

interface Spot {
  scenario: string;
  position: string;
  hand: string;
  depth_bb: number;
  num_players: number;
}
interface DrillResponse {
  enough_data: boolean;
  leak?: { scenario: string; position: string; kind: string | null };
  spots?: Spot[];
}
interface Grade {
  verdict: 'good' | 'thin' | 'leak';
  action: string;
  your_freq: number;
  chart_freq: { fold: number; call: number; raise: number };
  primary_action: string;
}

const SCENARIO_LABEL: Record<string, string> = {
  rfi: 'Opening from',
  vs_open: 'Facing a raise in',
  vs_3bet: 'Facing a 3-bet in',
};
const ACTIONS: Array<'fold' | 'call' | 'raise'> = ['fold', 'call', 'raise'];
const pct = (x: number) => Math.round(x * 100);

interface PreflopDrillProps {
  onBack: () => void;
}

export function PreflopDrill({ onBack }: PreflopDrillProps) {
  const [params] = useSearchParams();
  const scenario = params.get('scenario') || '';
  const position = params.get('position') || '';

  const [data, setData] = useState<DrillResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [idx, setIdx] = useState(0);
  const [grade, setGrade] = useState<Grade | null>(null);
  const [grading, setGrading] = useState(false);
  const [solid, setSolid] = useState(0); // 'good' answers
  const [answered, setAnswered] = useState(0);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    setData(null);
    setIdx(0);
    setGrade(null);
    setSolid(0);
    setAnswered(0);
    try {
      const qs = scenario && position ? `?scenario=${scenario}&position=${position}` : '';
      const resp = await fetch(`${config.API_URL}/api/coach/drill${qs}`, {
        credentials: 'include',
      });
      if (!resp.ok) throw new Error(`drill ${resp.status}`);
      setData(await resp.json());
    } catch (err) {
      logger.error('Failed to load drill:', err);
      setError('Could not load the drill.');
    } finally {
      setLoading(false);
    }
  }, [scenario, position]);

  useEffect(() => {
    load();
  }, [load]);

  const spots = data?.spots ?? [];
  const spot = spots[idx];
  const done = !!data?.enough_data && idx >= spots.length;

  const answer = async (action: 'fold' | 'call' | 'raise') => {
    if (!spot || grade || grading) return;
    setGrading(true);
    try {
      const resp = await fetch(`${config.API_URL}/api/coach/drill/answer`, {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          scenario: spot.scenario,
          position: spot.position,
          hand: spot.hand,
          action,
        }),
      });
      if (!resp.ok) throw new Error(`grade ${resp.status}`);
      const g: Grade = await resp.json();
      setGrade(g);
      setAnswered((n) => n + 1);
      if (g.verdict === 'good') setSolid((n) => n + 1);
    } catch (err) {
      logger.error('Failed to grade answer:', err);
    } finally {
      setGrading(false);
    }
  };

  const next = () => {
    setGrade(null);
    setIdx((i) => i + 1);
  };

  const spotLabel = (s: Spot) =>
    `${SCENARIO_LABEL[s.scenario] ?? s.scenario} ${s.position} · ${s.depth_bb}bb ${s.num_players}-max`;

  return (
    <>
      <MenuBar showUserInfo />
      <PageLayout variant="top" glowColor="emerald" maxWidth="sm" hasMenuBar>
        <BackButton onClick={onBack} />
        <PageHeader title="Preflop Drill" subtitle="Practice the spot you leak" titleVariant="primary" />

        {loading && <div className="pfd-state">Building your drill…</div>}
        {error && <div className="pfd-state pfd-error">{error}</div>}

        {data && !data.enough_data && (
          <div className="pfd-state">
            <Target size={28} />
            <p>No confirmed leak to drill yet. Play a bit more, then come back.</p>
          </div>
        )}

        {data?.enough_data && !done && spot && (
          <div className="pfd-body">
            <p className="pfd-spotline">{spotLabel(spot)}</p>
            <div className="pfd-hand">{spot.hand}</div>

            {!grade ? (
              <div className="pfd-actions">
                {ACTIONS.map((a) => (
                  <button key={a} className={`pfd-btn pfd-btn--${a}`} onClick={() => answer(a)} disabled={grading}>
                    {a === 'call' && spot.scenario === 'rfi' ? 'Limp/Call' : a[0].toUpperCase() + a.slice(1)}
                  </button>
                ))}
              </div>
            ) : (
              <div className={`pfd-feedback pfd-feedback--${grade.verdict}`}>
                <div className="pfd-verdict">
                  {grade.verdict === 'good' && 'Solid.'}
                  {grade.verdict === 'thin' && 'Thin — occasionally OK.'}
                  {grade.verdict === 'leak' && 'Leak — the solver rarely does this.'}
                </div>
                <div className="pfd-freqs">
                  solver: raise {pct(grade.chart_freq.raise)}% · call {pct(grade.chart_freq.call)}% · fold{' '}
                  {pct(grade.chart_freq.fold)}%
                </div>
                <button className="pfd-next" onClick={next}>
                  {idx + 1 >= spots.length ? 'Finish' : 'Next hand'}
                </button>
              </div>
            )}

            <p className="pfd-progress">
              Hand {Math.min(idx + 1, spots.length)} of {spots.length} · {solid}/{answered} solid
            </p>
          </div>
        )}

        {done && (
          <div className="pfd-state">
            <Target size={28} />
            <p>
              Drill complete — <strong>{solid}</strong> of <strong>{answered}</strong> solid.
            </p>
            <div className="pfd-end-actions">
              <button className="pfd-next" onClick={load}>
                Drill again
              </button>
              <button className="pfd-secondary" onClick={onBack}>
                Back to review
              </button>
            </div>
          </div>
        )}
      </PageLayout>
    </>
  );
}
