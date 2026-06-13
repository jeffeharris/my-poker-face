import { useCallback, useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { SlidersHorizontal, Shuffle } from 'lucide-react';
import { ActionButtons } from '../game/ActionButtons';
import { config } from '../../config';
import { logger } from '../../utils/logger';
import { SwipeDeck, type SwipeDir } from './swipe/SwipeDeck';
import { PreflopCardFace } from './preflop/PreflopCard';
import { VERDICT_HEADING, type Spot } from './preflop/preflopUtils';
import { DrillStage } from './DrillStage';
import { DrillResultOverlay } from './DrillResultOverlay';
import { useDrillRunner } from './useDrillRunner';

// Facing-a-raise drill: a player opened to ~2.5bb and it's on you — fold, call,
// or 3-bet. Swipe left = fold, up = call, right = 3-bet; the game's action bar
// mirrors it. Graded on the action vs the solver chart (vs_open). Built on the
// shared drill shell.

// Seats that can face an open (UTG acts first, so it never does).
const VS_OPEN_POS = ['HJ', 'CO', 'BTN', 'SB', 'BB'];
type Mode = 'random' | (typeof VS_OPEN_POS)[number];

// A plausible 100bb single-raised-pot to drive the game action bar. The drill
// grades the action, not the exact sizing, so a standard 2.5bb open is fine.
const BIG_BLIND = 100;
const OPEN_TO = 250; // villain opened to 2.5bb
const VS_OPEN_BETTING = {
  playerOptions: ['fold', 'call', 'raise'],
  currentPlayerStack: 100 * BIG_BLIND,
  highestBet: OPEN_TO,
  currentPlayerBet: 0,
  minRaise: OPEN_TO - BIG_BLIND, // min re-raise increment → min raise-to = 2*OPEN_TO - BB
  bigBlind: BIG_BLIND,
  potSize: BIG_BLIND + BIG_BLIND / 2 + OPEN_TO, // BB + SB + open
};

// Stable swipe ⇄ action maps (fold ←, call ↑, raise →).
const dirToAction = (dir: SwipeDir) => (dir === 'left' ? 'fold' : dir === 'up' ? 'call' : 'raise');
const actionToDir = (action: string): SwipeDir =>
  action === 'fold' ? 'left' : action === 'call' ? 'up' : 'right';

interface VsOpenDrillProps {
  onBack: () => void;
}

export function VsOpenDrill({ onBack }: VsOpenDrillProps) {
  const [params, setSearchParams] = useSearchParams();
  const paramPos = params.get('position') || '';
  const [mode, setMode] = useState<Mode>(VS_OPEN_POS.includes(paramPos) ? paramPos : 'random');
  const [showSettings, setShowSettings] = useState(false);

  const [pool, setPool] = useState<Spot[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const pickMode = (m: Mode) => {
    setMode(m);
    setShowSettings(false);
    setSearchParams(m === 'random' ? {} : { scenario: 'vs_open', position: m }, { replace: true });
  };

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    setPool([]);

    const fetchSpots = async (position: string): Promise<Spot[]> => {
      const resp = await fetch(
        `${config.API_URL}/api/coach/drill?scenario=vs_open&position=${position}`,
        { credentials: 'include' }
      );
      if (!resp.ok) throw new Error(`drill ${resp.status}`);
      const data = await resp.json();
      return (data.spots ?? []) as Spot[];
    };

    try {
      let spots: Spot[];
      if (mode === 'random') {
        const results = await Promise.allSettled(VS_OPEN_POS.map(fetchSpots));
        spots = results.flatMap((r) => (r.status === 'fulfilled' ? r.value : []));
      } else {
        spots = await fetchSpots(mode);
      }
      if (!spots.length) throw new Error('no spots');
      setPool(spots);
    } catch (err) {
      logger.error('Failed to load vs-open drill:', err);
      setError('Could not load the drill.');
    } finally {
      setLoading(false);
    }
  }, [mode]);

  useEffect(() => {
    load();
  }, [load]);

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
  const betting = useMemo(() => VS_OPEN_BETTING, []);

  return (
    <DrillStage
      title="Facing a Raise"
      onBack={onBack}
      subtitle="A player opened — fold, call, or 3-bet?"
      settings={
        <div className="swd-settings">
          <button
            type="button"
            className="swd-settings-toggle"
            onClick={() => setShowSettings((v) => !v)}
            aria-expanded={showSettings}
          >
            {mode === 'random' ? <Shuffle size={14} /> : <SlidersHorizontal size={14} />}
            {mode === 'random' ? 'Shuffle' : `${mode} only`}
          </button>
          {showSettings && (
            <div className="swd-pos-chips" role="radiogroup" aria-label="Position">
              {(['random', ...VS_OPEN_POS] as Mode[]).map((m) => (
                <button
                  key={m}
                  type="button"
                  role="radio"
                  aria-checked={mode === m}
                  aria-label={m === 'random' ? 'Shuffle positions' : m}
                  className={
                    'swd-pos-chip' +
                    (m === 'random' ? ' swd-pos-chip--shuffle' : '') +
                    (mode === m ? ' swd-pos-chip--active' : '')
                  }
                  onClick={() => pickMode(m)}
                >
                  {m === 'random' ? <Shuffle size={15} /> : m}
                </button>
              ))}
            </div>
          )}
        </div>
      }
      loading={loading}
      error={error}
      onRetry={load}
      ready={ready}
      deck={
        <SwipeDeck<Spot>
          ref={deckRef}
          draw={draw}
          renderFace={(spot) => <PreflopCardFace spot={spot} tag="Facing a raise" />}
          onSwipe={onSwipe}
          interactive={interactive}
          stamps={{ left: 'FOLD', right: 'RAISE', up: 'CALL' }}
        />
      }
      stats={`${solid}/${answered} solid · swipe ← ↑ → or use the bar`}
      control={<ActionButtons {...betting} onAction={onBarAction} inline noSizing />}
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
