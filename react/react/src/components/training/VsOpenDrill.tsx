import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { SlidersHorizontal, Shuffle } from 'lucide-react';
import { PageLayout, MenuBar } from '../shared';
import { ActionButtons } from '../game/ActionButtons';
import { config } from '../../config';
import { logger } from '../../utils/logger';
import { SwipeDeck, type SwipeDeckHandle, type SwipeDir } from './swipe/SwipeDeck';
import { PreflopCardFace, drawNext, pct, type Spot, type Grade } from './preflop/PreflopCard';

// Facing-a-raise drill: a player opened to ~2.5bb and it's on you — fold, call,
// or 3-bet. Swipe left = fold, right = raise, up = call; the game's action bar
// mirrors it (and lets you size a raise). Graded on the action vs the solver
// chart (vs_open). Built on the shared SwipeDeck + PreflopCard.

// Seats that can face an open (UTG acts first, so it never does).
const VS_OPEN_POS = ['HJ', 'CO', 'BTN', 'SB', 'BB'];
type Mode = 'random' | (typeof VS_OPEN_POS)[number];

const HOLD_MS: Record<Grade['verdict'], number> = { good: 700, thin: 1050, leak: 1800 };

// A plausible 100bb single-raised-pot to drive the game action bar. The drill
// grades the action, not the exact sizing, so a standard 2.5bb open is fine.
const BIG_BLIND = 100;
const OPEN_TO = 250; // villain opened to 2.5bb
function bettingProps(spot: Spot) {
  const stack = spot.depth_bb * BIG_BLIND;
  return {
    playerOptions: ['fold', 'call', 'raise'],
    currentPlayerStack: stack,
    highestBet: OPEN_TO,
    currentPlayerBet: 0,
    minRaise: OPEN_TO - BIG_BLIND, // min re-raise increment → min raise-to = 2*OPEN_TO - BB
    bigBlind: BIG_BLIND,
    potSize: BIG_BLIND + BIG_BLIND / 2 + OPEN_TO, // BB + SB + open
  };
}

// Swipe direction ⇄ poker action.
const DIR_ACTION: Record<SwipeDir, string> = { left: 'fold', right: 'raise', up: 'call' };
function actionToDir(action: string): SwipeDir {
  if (action === 'fold') return 'left';
  if (action === 'call') return 'up';
  return 'right'; // raise / all_in
}

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

  const [grade, setGrade] = useState<Grade | null>(null);
  const [grading, setGrading] = useState(false);
  const [solid, setSolid] = useState(0);
  const [answered, setAnswered] = useState(0);

  const deckRef = useRef<SwipeDeckHandle>(null);

  const pickMode = (m: Mode) => {
    setMode(m);
    setShowSettings(false);
    setSearchParams(m === 'random' ? {} : { scenario: 'vs_open', position: m }, { replace: true });
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

  const draw = useCallback((avoid: Spot | null) => drawNext(pool, avoid), [pool]);

  const onSwipe = useCallback(async (spot: Spot, dir: SwipeDir) => {
    const action = DIR_ACTION[dir];
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

  // The action bar feeds the deck: tapping a button flings the card the matching
  // way, which grades + advances through the same path as a swipe.
  const onBarAction = useCallback(
    (action: string) => {
      if (grade || grading) return;
      deckRef.current?.swipe(actionToDir(action));
    },
    [grade, grading]
  );

  const front = pool.length > 0;
  const ready = !loading && !error && front;

  // Keyboard parity.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (grade || grading) return;
      if (e.key === 'ArrowRight') deckRef.current?.swipe('right');
      if (e.key === 'ArrowLeft') deckRef.current?.swipe('left');
      if (e.key === 'ArrowUp') deckRef.current?.swipe('up');
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [grade, grading]);

  const bp = useMemo(() => bettingProps({ depth_bb: 100 } as Spot), []);

  return (
    <>
      <MenuBar onBack={onBack} title="Facing a Raise" showUserInfo onMainMenu={onBack} />
      <PageLayout variant="top" glowColor="emerald" maxWidth="md" hasMenuBar>
        <p className="swd-subtitle">A player opened — fold, call, or 3-bet?</p>

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
              renderFace={(spot) => <PreflopCardFace spot={spot} tag="Facing a raise" />}
              onSwipe={onSwipe}
              interactive={interactive}
              stamps={{ left: 'FOLD', right: 'RAISE', up: 'CALL' }}
            />

            <p className="swd-stats">
              {solid}/{answered} solid · swipe ← ↑ → or use the bar
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
                    solver: raise {pct(grade.chart_freq.raise)}% · call {pct(grade.chart_freq.call)}
                    % · fold {pct(grade.chart_freq.fold)}%
                  </div>
                </button>
              ) : (
                <ActionButtons {...bp} onAction={onBarAction} inline noSizing />
              )}
            </div>
          </div>
        )}
      </PageLayout>
    </>
  );
}
