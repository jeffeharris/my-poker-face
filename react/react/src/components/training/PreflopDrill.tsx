import { useCallback, useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { Target } from 'lucide-react';
import { PageLayout, PageHeader, MenuBar, BackButton } from '../shared';
import { Card } from '../cards';
import { config } from '../../config';
import { logger } from '../../utils/logger';
import './PreflopDrill.css';

// A 169-hand shorthand → two concrete cards for display. Suited = same suit;
// offsuit / pair = two different suits (black + red reads clearest). 'T' → '10'
// (the Card component's rank notation).
function handToCards(hand: string): [{ rank: string; suit: string }, { rank: string; suit: string }] {
  const isPair = hand.length === 2;
  const norm = (r: string) => (r === 'T' ? '10' : r);
  const r1 = norm(hand[0]);
  const r2 = norm(isPair ? hand[0] : hand[1]);
  const suited = !isPair && hand[2] === 's';
  return [
    { rank: r1, suit: 'Spades' },
    { rank: r2, suit: suited ? 'Spades' : 'Hearts' },
  ];
}

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

// Spot picker — drill ANY spot, not just your confirmed leaks.
const PICK_SCENARIOS: Array<[string, string]> = [
  ['rfi', 'Opening (folded to you)'],
  ['vs_open', 'Facing a raise'],
  ['vs_3bet', 'Facing a 3-bet'],
];
const ALL_POS = ['UTG', 'HJ', 'CO', 'BTN', 'SB', 'BB'];
// The BB never opens, so there's no rfi chart for it.
const posOptions = (s: string) => (s === 'rfi' ? ALL_POS.filter((p) => p !== 'BB') : ALL_POS);

function SpotPicker({ onPick }: { onPick: (scenario: string, position: string) => void }) {
  const [s, setS] = useState('rfi');
  const [p, setP] = useState('BTN');
  const onScenario = (v: string) => {
    setS(v);
    if (!posOptions(v).includes(p)) setP(posOptions(v)[0]);
  };
  return (
    <div className="pfd-picker">
      <label className="pfd-picker-field">
        Spot
        <select value={s} onChange={(e) => onScenario(e.target.value)}>
          {PICK_SCENARIOS.map(([v, l]) => (
            <option key={v} value={v}>
              {l}
            </option>
          ))}
        </select>
      </label>
      <label className="pfd-picker-field">
        Position
        <select value={p} onChange={(e) => setP(e.target.value)}>
          {posOptions(s).map((pos) => (
            <option key={pos} value={pos}>
              {pos}
            </option>
          ))}
        </select>
      </label>
      <button type="button" className="pfd-next" onClick={() => onPick(s, p)}>
        Start drill
      </button>
    </div>
  );
}

interface PreflopDrillProps {
  onBack: () => void;
}

export function PreflopDrill({ onBack }: PreflopDrillProps) {
  const [params, setSearchParams] = useSearchParams();
  const scenario = params.get('scenario') || '';
  const position = params.get('position') || '';
  const [showPicker, setShowPicker] = useState(false);

  const pickSpot = (s: string, p: string) => {
    setSearchParams({ scenario: s, position: p }); // re-fetches via load's deps
    setShowPicker(false);
  };

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

        {/* Always offer picking any spot (not just your confirmed leaks). */}
        <div className="pfd-pick-bar">
          <button
            type="button"
            className="pfd-pick-toggle"
            onClick={() => setShowPicker((v) => !v)}
            aria-expanded={showPicker}
          >
            {showPicker ? 'Hide spot picker' : 'Practice a specific spot ▾'}
          </button>
          {showPicker && <SpotPicker onPick={pickSpot} />}
        </div>

        {loading && <div className="pfd-state">Building your drill…</div>}
        {error && <div className="pfd-state pfd-error">{error}</div>}

        {data && !data.enough_data && (
          <div className="pfd-state">
            <Target size={28} />
            <p>No confirmed leak to drill yet — pick any spot to practice:</p>
            <SpotPicker onPick={pickSpot} />
          </div>
        )}

        {data?.enough_data && !done && spot && (
          <div className="pfd-body">
            <p className="pfd-spotline">{spotLabel(spot)}</p>
            <div className="pfd-hand">
              <div className="pfd-cards">
                {handToCards(spot.hand).map((c, i) => (
                  <Card key={i} card={c} faceDown={false} size="small" />
                ))}
              </div>
              <div className="pfd-hand-label">{spot.hand}</div>
            </div>

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
