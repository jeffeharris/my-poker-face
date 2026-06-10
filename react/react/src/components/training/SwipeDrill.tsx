import { useCallback, useEffect, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { SlidersHorizontal, Shuffle } from 'lucide-react';
import { PageLayout, MenuBar } from '../shared';
import { ActionButtons } from '../game/ActionButtons';
import { config } from '../../config';
import { logger } from '../../utils/logger';
import { SwipeDeck, type SwipeDeckHandle, type SwipeDir } from './swipe/SwipeDeck';
import {
  PreflopCardFace,
  drawNext,
  pct,
  RFI_POS,
  type Spot,
  type Grade,
} from './preflop/PreflopCard';

// Opening (RFI) drill: folded to you — open or fold. Binary swipe (left = fold,
// right = open), graded against the solver chart. Built on the shared SwipeDeck
// carousel + PreflopCard face.

type Mode = 'random' | (typeof RFI_POS)[number];

// How long the verdict flashes before the next card deals. Wrong answers linger
// so you actually read the correction; tapping the flash skips the wait.
const HOLD_MS: Record<Grade['verdict'], number> = { good: 700, thin: 1050, leak: 1800 };

// Folded to you, 100bb, facing only the big blind — drives the game action bar
// (fold / open). The drill grades the action, not sizing (noSizing), so a fixed
// context is fine.
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

  const [grade, setGrade] = useState<Grade | null>(null);
  const [grading, setGrading] = useState(false);
  const [solid, setSolid] = useState(0);
  const [answered, setAnswered] = useState(0);

  const deckRef = useRef<SwipeDeckHandle>(null);

  const pickMode = (m: Mode) => {
    setMode(m);
    setShowSettings(false);
    setSearchParams(m === 'random' ? {} : { scenario: 'rfi', position: m }, { replace: true });
  };

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    setPool([]);
    setGrade(null);
    setSolid(0);
    setAnswered(0);

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

  const draw = useCallback((avoid: Spot | null) => drawNext(pool, avoid), [pool]);

  const onSwipe = useCallback(async (spot: Spot, dir: SwipeDir) => {
    const action = dir === 'right' ? 'raise' : 'fold';
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
  }, []);

  const interactive = !grade && !grading;

  const next = useCallback(() => {
    setGrade(null);
    deckRef.current?.advance();
  }, []);

  useEffect(() => {
    if (!grade) return;
    const t = setTimeout(next, HOLD_MS[grade.verdict]);
    return () => clearTimeout(t);
  }, [grade, next]);

  // Action bar + keyboard feed the deck: fold → left, open (raise) → right.
  const onBarAction = useCallback(
    (action: string) => {
      if (grade || grading) return;
      deckRef.current?.swipe(action === 'fold' ? 'left' : 'right');
    },
    [grade, grading]
  );

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (grade || grading) return;
      if (e.key === 'ArrowRight') deckRef.current?.swipe('right');
      if (e.key === 'ArrowLeft') deckRef.current?.swipe('left');
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [grade, grading]);

  const ready = !loading && !error && pool.length > 0;

  return (
    <>
      <MenuBar onBack={onBack} title="Opening Drill" showUserInfo onMainMenu={onBack} />
      <PageLayout variant="top" glowColor="emerald" maxWidth="md" hasMenuBar>
        <p className="swd-subtitle">Folded to you — open or fold?</p>

        {/* Position setting — defaults to a shuffle of all positions. */}
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

        {loading && <div className="swd-state">Dealing your spots…</div>}
        {error && (
          <div className="swd-state swd-error">
            <p>{error}</p>
            <button className="swd-next" onClick={load}>
              Try again
            </button>
          </div>
        )}

        {ready && (
          <div className="swd-body">
            <SwipeDeck<Spot>
              ref={deckRef}
              draw={draw}
              renderFace={(spot) => <PreflopCardFace spot={spot} tag="Folded to you" />}
              onSwipe={onSwipe}
              interactive={interactive}
              stamps={{ left: 'FOLD', right: 'OPEN' }}
            />

            <p className="swd-stats">
              {solid}/{answered} solid · swipe or use ← →
            </p>

            <div className="pf-control">
              {grade ? (
                <button
                  type="button"
                  className={`swd-feedback swd-feedback--${grade.verdict}`}
                  onClick={next}
                  aria-label="Continue to next hand"
                >
                  <div className="swd-verdict">
                    {grade.verdict === 'good' && 'Solid.'}
                    {grade.verdict === 'thin' && 'Thin — occasionally OK.'}
                    {grade.verdict === 'leak' && 'Leak — the solver rarely does this.'}
                  </div>
                  <div className="swd-freqs">
                    solver: open {pct(grade.chart_freq.raise)}% · fold {pct(grade.chart_freq.fold)}%
                  </div>
                </button>
              ) : (
                <ActionButtons {...RFI_BETTING} onAction={onBarAction} inline noSizing />
              )}
            </div>
          </div>
        )}
      </PageLayout>
    </>
  );
}
