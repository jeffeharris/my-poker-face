import { useCallback, useEffect, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { SlidersHorizontal, Shuffle } from 'lucide-react';
import { PageLayout, MenuBar } from '../shared';
import { Card } from '../cards';
import { config } from '../../config';
import { logger } from '../../utils/logger';
import { SwipeDeck, type SwipeDeckHandle, type SwipeDir } from './swipe/SwipeDeck';
import './SwipeDrill.css';

// A 169-hand shorthand → two concrete cards for display. Suited = same suit;
// offsuit / pair = two different suits (black + red reads clearest). 'T' → '10'
// (the Card component's rank notation).
function handToCards(
  hand: string
): [{ rank: string; suit: string }, { rank: string; suit: string }] {
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
interface Grade {
  verdict: 'good' | 'thin' | 'leak';
  action: string;
  your_freq: number;
  chart_freq: { fold: number; call: number; raise: number };
  primary_action: string;
}

// Long-form position names for the situation card.
const POSITION_NAME: Record<string, string> = {
  UTG: 'Under the gun',
  HJ: 'Hijack',
  CO: 'Cutoff',
  BTN: 'Button',
  SB: 'Small blind',
};
const ALL_POS = ['UTG', 'HJ', 'CO', 'BTN', 'SB']; // BB never opens — no rfi chart
type Mode = 'random' | (typeof ALL_POS)[number];

// Table seats in clockwise order from the button.
const SEAT_ORDER = ['BTN', 'SB', 'BB', 'UTG', 'HJ', 'CO'];
// Six screen slots, clockwise starting from the bottom (where YOU always sit).
const SLOTS: { x: number; y: number }[] = [
  { x: 50, y: 94 }, // 0 bottom — hero
  { x: 10, y: 70 }, // 1 bottom-left
  { x: 10, y: 30 }, // 2 top-left
  { x: 50, y: 6 }, //  3 top
  { x: 90, y: 30 }, // 4 top-right
  { x: 90, y: 70 }, // 5 bottom-right
];

// Mini table map: YOU are the filled emerald dot, fixed at the bottom; the red
// dealer button moves around you by your seat's offset from the button.
function SeatMap({ position }: { position: string }) {
  const h = SEAT_ORDER.indexOf(position);
  const dealerSlot = h < 0 ? 0 : (6 - h) % 6; // clockwise seats from you to the button
  const here = POSITION_NAME[position] ?? position;
  return (
    <div className="oc-table" aria-label={`You are in ${here}; the dealer button is the red seat`}>
      {SLOTS.map((s, i) => (
        <span
          key={i}
          aria-hidden="true"
          className={
            'oc-seat' +
            (i === 0 ? ' oc-seat--hero' : '') +
            (i === dealerSlot ? ' oc-seat--dealer' : '')
          }
          style={{ left: `${s.x}%`, top: `${s.y}%` }}
        />
      ))}
      <span className="oc-table__code" aria-hidden="true">
        {position}
      </span>
    </div>
  );
}

// The opening-drill card face. Follows the shared drill-card anatomy:
//   situation (top)  →  your cards (middle).
// Options + running stats live below the card (in the drill screen).
function OpeningCardFace({ spot }: { spot: Spot }) {
  return (
    <>
      <div className="oc-situation">
        <span className="oc-pos">{POSITION_NAME[spot.position] ?? spot.position}</span>
        <SeatMap position={spot.position} />
        <div className="oc-context">
          <span className="oc-chip">{spot.depth_bb}bb deep</span>
          <span className="oc-chip">{spot.num_players}-max</span>
          <span className="oc-chip oc-chip--folded">Folded to you</span>
        </div>
      </div>
      <div className="oc-cards">
        <div className="oc-holes">
          {handToCards(spot.hand).map((c, i) => (
            <Card key={i} card={c} faceDown={false} size="xlarge" />
          ))}
        </div>
        <div className="oc-hand">{spot.hand}</div>
      </div>
    </>
  );
}

const pct = (x: number) => Math.round(x * 100);

// How long the verdict flashes before the next card deals. Wrong answers linger
// so you actually read the correction; tapping the flash skips the wait.
const HOLD_MS: Record<Grade['verdict'], number> = { good: 700, thin: 1050, leak: 1800 };

const sameSpot = (a: Spot, b: Spot) => a.position === b.position && a.hand === b.hand;

// Draw a random spot from the pool, avoiding an immediate repeat.
function drawNext(pool: Spot[], avoid?: Spot | null): Spot | null {
  if (pool.length === 0) return null;
  if (pool.length === 1) return pool[0];
  let pick = pool[Math.floor(Math.random() * pool.length)];
  while (avoid && sameSpot(pick, avoid)) pick = pool[Math.floor(Math.random() * pool.length)];
  return pick;
}

interface SwipeDrillProps {
  onBack: () => void;
}

export function SwipeDrill({ onBack }: SwipeDrillProps) {
  const [params, setSearchParams] = useSearchParams();
  // Mode comes from the URL on first load (a leak nudge may deep-link a single
  // position); otherwise we mix all positions so the player just starts drilling.
  const paramPos = params.get('position') || '';
  const [mode, setMode] = useState<Mode>(ALL_POS.includes(paramPos) ? paramPos : 'random');
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
        const results = await Promise.allSettled(ALL_POS.map(fetchSpots));
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

  // The deck draws from the pool. Memoized so it only rebuilds when the pool
  // changes (a mode reload), not on every render.
  const draw = useCallback((avoid: Spot | null) => drawNext(pool, avoid), [pool]);

  // Grade the swiped card against the solver chart. The card has already flung
  // off; the verdict flashes, then auto-advance rises the next card.
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

  // Auto-advance after a verdict — no button to press. Wrong answers linger.
  useEffect(() => {
    if (!grade) return;
    const t = setTimeout(next, HOLD_MS[grade.verdict]);
    return () => clearTimeout(t);
  }, [grade, next]);

  const triggerFront = useCallback(
    (dir: SwipeDir) => {
      if (grade || grading) return;
      deckRef.current?.swipe(dir);
    },
    [grade, grading]
  );

  // Keyboard parity: ← fold, → open.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'ArrowRight') triggerFront('right');
      if (e.key === 'ArrowLeft') triggerFront('left');
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [triggerFront]);

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
              {(['random', ...ALL_POS] as Mode[]).map((m) => (
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
              renderFace={(spot) => <OpeningCardFace spot={spot} />}
              onSwipe={onSwipe}
              interactive={interactive}
              stamps={{ left: 'FOLD', right: 'OPEN' }}
            />

            {/* Stats — between the card and the options. */}
            <p className="swd-stats">
              {solid}/{answered} solid · swipe or use ← →
            </p>

            {/* Options (bottom). Verdict flashes here then auto-advances; tap to skip. */}
            <div className="swd-control">
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
                <div className="swd-actions">
                  <button
                    className="swd-btn swd-btn--fold"
                    onClick={() => triggerFront('left')}
                    disabled={!interactive}
                  >
                    ← Fold
                  </button>
                  <button
                    className="swd-btn swd-btn--raise"
                    onClick={() => triggerFront('right')}
                    disabled={!interactive}
                  >
                    Open →
                  </button>
                </div>
              )}
            </div>
          </div>
        )}
      </PageLayout>
    </>
  );
}
