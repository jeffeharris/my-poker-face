import { useCallback, useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { Target } from 'lucide-react';
import { ActionButtons } from '../game/ActionButtons';
import { config } from '../../config';
import { logger } from '../../utils/logger';
import { SwipeDeck, type SwipeDir } from './swipe/SwipeDeck';
import { PreflopCardFace } from './preflop/PreflopCard';
import { VERDICT_HEADING, type Spot } from './preflop/preflopUtils';
import { DrillStage } from './DrillStage';
import { DrillResultOverlay } from './DrillResultOverlay';
import { useDrillRunner } from './useDrillRunner';
import './PreflopDrill.css';

// Leak drill: practice the spot the finder says you leak (or any spot you pick).
// Same swipe UX as the other drills — fold ←, call/limp ↑, 3-bet/raise → —
// graded vs the solver chart. Built on the shared drill shell.

interface DrillResponse {
  enough_data: boolean;
  leak?: { scenario: string; position: string; kind: string | null };
  spots?: Spot[];
}

const SCENARIO_TAG: Record<string, string> = {
  rfi: 'Folded to you',
  vs_open: 'Facing a raise',
  vs_3bet: 'Facing a 3-bet',
  vs_4bet: 'Facing a 4-bet',
};

// Spot picker — drill ANY spot, not just your confirmed leaks.
const PICK_SCENARIOS: Array<[string, string]> = [
  ['rfi', 'Opening (folded to you)'],
  ['vs_open', 'Facing a raise'],
  ['vs_3bet', 'Facing a 3-bet'],
  ['vs_4bet', 'Facing a 4-bet'],
];
const ALL_POS = ['UTG', 'HJ', 'CO', 'BTN', 'SB', 'BB'];
// The BB never opens, so there's no rfi chart for it.
const posOptions = (s: string) => (s === 'rfi' ? ALL_POS.filter((p) => p !== 'BB') : ALL_POS);

// A 100bb single-raised pot drives the action bar (fold / call / 3-bet). The
// drill grades the action, not sizing (noSizing), so a fixed context is fine.
const BIG_BLIND = 100;
const OPEN_TO = 250;
const DRILL_BETTING = {
  playerOptions: ['fold', 'call', 'raise'],
  currentPlayerStack: 100 * BIG_BLIND,
  highestBet: OPEN_TO,
  currentPlayerBet: 0,
  minRaise: OPEN_TO - BIG_BLIND,
  bigBlind: BIG_BLIND,
  potSize: BIG_BLIND + BIG_BLIND / 2 + OPEN_TO,
};

const dirToAction = (dir: SwipeDir) => (dir === 'left' ? 'fold' : dir === 'up' ? 'call' : 'raise');
const actionToDir = (action: string): SwipeDir =>
  action === 'fold' ? 'left' : action === 'call' ? 'up' : 'right';

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
      <button type="button" className="swd-next" onClick={() => onPick(s, p)}>
        Drill this spot
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

  const [data, setData] = useState<DrillResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const pickSpot = (s: string, p: string) => {
    setShowPicker(false);
    setSearchParams({ scenario: s, position: p }); // re-fetches via load's deps
  };

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    setData(null);
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

  const pool = useMemo(() => (data?.enough_data ? (data.spots ?? []) : []), [data]);
  const runner = useDrillRunner(pool, { dirToAction, actionToDir, allowUp: true });
  const {
    grade,
    chosenDir,
    draw,
    deckRef,
    interactive,
    onSwipe,
    next,
    onBarAction,
    solid,
    answered,
  } = runner;

  const ready = !loading && !error && pool.length > 0;
  const tag = SCENARIO_TAG[scenario] ?? 'Facing a raise';
  const spotLabel =
    scenario && position ? `${SCENARIO_TAG[scenario] ?? scenario} · ${position}` : '';

  return (
    <DrillStage
      title="Preflop Drill"
      onBack={onBack}
      subtitle={spotLabel || 'Practice the spot you leak'}
      settings={
        <div className="swd-settings">
          <button
            type="button"
            className="swd-settings-toggle"
            onClick={() => setShowPicker((v) => !v)}
            aria-expanded={showPicker}
          >
            {showPicker ? 'Hide spot picker' : 'Practice a specific spot ▾'}
          </button>
          {showPicker && <SpotPicker onPick={pickSpot} />}
        </div>
      }
      loading={loading}
      error={error}
      onRetry={load}
      ready={ready}
      empty={
        data && !data.enough_data ? (
          <div className="swd-state">
            <Target size={28} />
            <p>No confirmed leak to drill yet — pick any spot to practice:</p>
            <SpotPicker onPick={pickSpot} />
          </div>
        ) : null
      }
      deck={
        <SwipeDeck<Spot>
          ref={deckRef}
          draw={draw}
          renderFace={(spot) => <PreflopCardFace spot={spot} tag={tag} />}
          onSwipe={onSwipe}
          interactive={interactive}
          stamps={{ left: 'FOLD', right: 'RAISE', up: 'CALL' }}
        />
      }
      stats={`${solid}/${answered} solid · swipe ← ↑ → or use the bar`}
      control={<ActionButtons {...DRILL_BETTING} onAction={onBarAction} inline noSizing />}
      overlay={
        grade && (
          <DrillResultOverlay
            verdict={grade.verdict}
            heading={VERDICT_HEADING[grade.verdict]}
            chosen={chosenDir}
            freqs={[
              { dir: 'left', label: 'Fold', value: grade.chart_freq.fold },
              { dir: 'up', label: 'Call', value: grade.chart_freq.call },
              { dir: 'right', label: 'Raise', value: grade.chart_freq.raise },
            ]}
            onDone={next}
          />
        )
      }
    />
  );
}
