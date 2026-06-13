import { useCallback, useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { SlidersHorizontal, Shuffle } from 'lucide-react';
import { ActionButtons } from '../game/ActionButtons';
import { config } from '../../config';
import { logger } from '../../utils/logger';
import { SwipeDeck, type SwipeDir } from './swipe/SwipeDeck';
import { PreflopCardFace } from './preflop/PreflopCard';
import { RFI_POS, VERDICT_HEADING, type Spot } from './preflop/preflopUtils';
import { DrillStage } from './DrillStage';
import { DrillResultOverlay } from './DrillResultOverlay';
import { useDrillRunner } from './useDrillRunner';

// Opening (RFI) drill: folded to you — open or fold. Binary swipe (left = fold,
// right = open), graded against the solver chart. Built on the shared drill
// shell (DrillStage + useDrillRunner + SwipeDeck + PreflopCard).

type Mode = 'random' | (typeof RFI_POS)[number];

// Folded to you, 100bb, facing only the big blind — drives the game action bar
// (fold / open). The drill grades the action, not sizing (noSizing).
const BIG_BLIND = 100;
const RFI_BETTING = {
  playerOptions: ['fold', 'raise'],
  currentPlayerStack: 100 * BIG_BLIND,
  highestBet: BIG_BLIND,
  currentPlayerBet: 0,
  minRaise: BIG_BLIND,
  bigBlind: BIG_BLIND,
  potSize: BIG_BLIND + BIG_BLIND / 2, // BB + SB
};

// Stable swipe ⇄ action maps (open = raise; everything else = fold).
const dirToAction = (dir: SwipeDir) => (dir === 'right' ? 'raise' : 'fold');
const actionToDir = (action: string): SwipeDir => (action === 'fold' ? 'left' : 'right');

interface SwipeDrillProps {
  onBack: () => void;
}

export function SwipeDrill({ onBack }: SwipeDrillProps) {
  const [params, setSearchParams] = useSearchParams();
  // Mode comes from the URL on first load (a leak nudge may deep-link a single
  // position); otherwise we mix all positions so the player just starts drilling.
  const paramPos = params.get('position') || '';
  const [mode, setMode] = useState<Mode>(RFI_POS.includes(paramPos) ? paramPos : 'random');
  const [showSettings, setShowSettings] = useState(false);

  const [pool, setPool] = useState<Spot[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const pickMode = (m: Mode) => {
    setMode(m);
    setShowSettings(false);
    setSearchParams(m === 'random' ? {} : { scenario: 'rfi', position: m }, { replace: true });
  };

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    setPool([]);

    const fetchSpots = async (position: string): Promise<Spot[]> => {
      const resp = await fetch(
        `${config.API_URL}/api/coach/drill?scenario=rfi&position=${position}`,
        { credentials: 'include' }
      );
      if (!resp.ok) throw new Error(`drill ${resp.status}`);
      const data = await resp.json();
      return (data.spots ?? []) as Spot[];
    };

    try {
      let spots: Spot[];
      if (mode === 'random') {
        const results = await Promise.allSettled(RFI_POS.map(fetchSpots));
        spots = results.flatMap((r) => (r.status === 'fulfilled' ? r.value : []));
      } else {
        spots = await fetchSpots(mode);
      }
      if (!spots.length) throw new Error('no spots');
      setPool(spots);
    } catch (err) {
      logger.error('Failed to load swipe drill:', err);
      setError('Could not load the drill.');
    } finally {
      setLoading(false);
    }
  }, [mode]);

  useEffect(() => {
    load();
  }, [load]);

  const runner = useDrillRunner(pool, { dirToAction, actionToDir });
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

  return (
    <DrillStage
      title="Opening Drill"
      onBack={onBack}
      subtitle="Folded to you — open or fold?"
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
              {(['random', ...RFI_POS] as Mode[]).map((m) => (
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
          renderFace={(spot) => <PreflopCardFace spot={spot} tag="Folded to you" />}
          onSwipe={onSwipe}
          interactive={interactive}
          stamps={{ left: 'FOLD', right: 'OPEN' }}
        />
      }
      stats={`${solid}/${answered} solid · swipe or use ← →`}
      control={<ActionButtons {...RFI_BETTING} onAction={onBarAction} inline noSizing />}
      overlay={
        grade && (
          <DrillResultOverlay
            verdict={grade.verdict}
            heading={VERDICT_HEADING[grade.verdict]}
            chosen={chosenDir}
            freqs={[
              { dir: 'left', label: 'Fold', value: grade.chart_freq.fold },
              { dir: 'right', label: 'Open', value: grade.chart_freq.raise },
            ]}
            onDone={next}
          />
        )
      }
    />
  );
}
