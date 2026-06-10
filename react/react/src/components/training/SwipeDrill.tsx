import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
} from 'react';
import { useSearchParams } from 'react-router-dom';
import { motion, useMotionValue, useTransform, animate, type PanInfo } from 'framer-motion';
import { SlidersHorizontal, Shuffle } from 'lucide-react';
import { PageLayout, MenuBar } from '../shared';
import { Card } from '../cards';
import { config } from '../../config';
import { logger } from '../../utils/logger';
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
type Action = 'fold' | 'raise';

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
    <div className="dc-table" aria-label={`You are in ${here}; the dealer button is the red seat`}>
      {SLOTS.map((s, i) => (
        <span
          key={i}
          aria-hidden="true"
          className={
            'dc-seat' +
            (i === 0 ? ' dc-seat--hero' : '') +
            (i === dealerSlot ? ' dc-seat--dealer' : '')
          }
          style={{ left: `${s.x}%`, top: `${s.y}%` }}
        />
      ))}
      <span className="dc-table__code" aria-hidden="true">
        {position}
      </span>
    </div>
  );
}

// The card's inner face — identical for every card in the stack.
function CardFace({ spot }: { spot: Spot }) {
  return (
    <div className="dc-card__body">
      <div className="dc-card__head">
        <span className="dc-card__pos">{POSITION_NAME[spot.position] ?? spot.position}</span>
        <SeatMap position={spot.position} />
      </div>
      <div className="dc-holes">
        {handToCards(spot.hand).map((c, i) => (
          <Card key={i} card={c} faceDown={false} size="xlarge" />
        ))}
      </div>
      <div className="dc-card__hand">{spot.hand}</div>
      <div className="dc-card__meta">
        <span className="dc-chip">{spot.depth_bb}bb deep</span>
        <span className="dc-chip">{spot.num_players}-max</span>
        <span className="dc-chip dc-chip--folded">Folded to you</span>
      </div>
    </div>
  );
}

const pct = (x: number) => Math.round(x * 100);
const SWIPE_THRESHOLD = 110; // px past which a release commits the swipe
const STACK_SIZE = 5; // cards held in the ring buffer (front + peeks + hidden preloaders)

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

// Resting transform for a card at a given depth in the stack. Depth 0 is the
// front (interactive); deeper cards sit lower, smaller, and eventually hidden —
// but still rendered so their images preload before they reach the front.
function depthStyle(depth: number) {
  return {
    scale: Math.max(1 - depth * 0.04, 0.84),
    y: depth * 11,
    opacity: depth >= 3 ? 0 : 1,
  };
}

interface SwipeHandle {
  fling: (action: Action) => void;
}
interface SwipeCardProps {
  spot: Spot;
  depth: number;
  interactive: boolean;
  onCommit: (action: Action, spot: Spot) => void;
}

// One card in the stack. Each owns its motion state (so the shared-x reset that
// would snap a recycled card never happens) and keeps a STABLE React key, so as
// the stack reorders a card animates between depths without ever remounting —
// the peek literally rises into the front slot. No content swap, no flash.
const SwipeCard = forwardRef<SwipeHandle, SwipeCardProps>(function SwipeCard(
  { spot, depth, interactive, onCommit },
  ref
) {
  const x = useMotionValue(0);
  const rotate = useTransform(x, [-260, 260], [-11, 11]);
  const openStamp = useTransform(x, [25, SWIPE_THRESHOLD], [0, 1]);
  const foldStamp = useTransform(x, [-SWIPE_THRESHOLD, -25], [1, 0]);

  const fling = useCallback(
    (action: Action) => {
      animate(x, action === 'raise' ? 640 : -640, { duration: 0.26, ease: 'easeOut' });
      onCommit(action, spot);
    },
    [x, onCommit, spot]
  );

  useImperativeHandle(ref, () => ({ fling }), [fling]);

  const onDragEnd = (_e: unknown, info: PanInfo) => {
    const past = Math.abs(info.offset.x) > SWIPE_THRESHOLD || Math.abs(info.velocity.x) > 500;
    if (past) fling(info.offset.x > 0 ? 'raise' : 'fold');
    else animate(x, 0, { type: 'spring', stiffness: 500, damping: 40 });
  };

  const ds = depthStyle(depth);
  return (
    <motion.div
      className="dc-card"
      style={{ x, rotate, zIndex: STACK_SIZE - depth }}
      initial={ds}
      animate={ds}
      transition={{ type: 'spring', stiffness: 300, damping: 30 }}
      drag={interactive ? 'x' : false}
      dragConstraints={{ left: 0, right: 0 }}
      dragElastic={0.6}
      onDragEnd={interactive ? onDragEnd : undefined}
      whileTap={interactive ? { cursor: 'grabbing' } : undefined}
    >
      {depth === 0 && (
        <>
          <motion.span className="dc-stamp dc-stamp--open" style={{ opacity: openStamp }}>
            OPEN
          </motion.span>
          <motion.span className="dc-stamp dc-stamp--fold" style={{ opacity: foldStamp }}>
            FOLD
          </motion.span>
        </>
      )}
      <CardFace spot={spot} />
    </motion.div>
  );
});

interface StackCard {
  key: number;
  spot: Spot;
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

  // The drawable pool + the live ring buffer of cards. nextKey hands out stable
  // keys so each card element persists across reorders.
  const poolRef = useRef<Spot[]>([]);
  const nextKey = useRef(0);
  const [stack, setStack] = useState<StackCard[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [grade, setGrade] = useState<Grade | null>(null);
  const [grading, setGrading] = useState(false);
  const [solid, setSolid] = useState(0);
  const [answered, setAnswered] = useState(0);

  // Imperative handles to each live card, so buttons / arrow keys can fling the
  // front card with the same animation as a drag-release.
  const cardRefs = useRef(new Map<number, SwipeHandle>());

  const pickMode = (m: Mode) => {
    setMode(m);
    setShowSettings(false);
    setSearchParams(m === 'random' ? {} : { scenario: 'rfi', position: m }, { replace: true });
  };

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    setStack([]);
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
      let deck: Spot[];
      if (mode === 'random') {
        const results = await Promise.allSettled(ALL_POS.map(fetchSpots));
        deck = results.flatMap((r) => (r.status === 'fulfilled' ? r.value : []));
      } else {
        deck = await fetchSpots(mode);
      }
      if (!deck.length) throw new Error('no spots');
      poolRef.current = deck;
      const initial: StackCard[] = [];
      let prev: Spot | null = null;
      for (let i = 0; i < STACK_SIZE; i++) {
        const s = drawNext(deck, prev);
        if (!s) break;
        initial.push({ key: nextKey.current++, spot: s });
        prev = s;
      }
      setStack(initial);
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

  // Advance the ring: drop the front card (it has flown off), shift everyone up a
  // depth (the peek rises into the front slot — same element, no remount), and
  // recycle a fresh card onto the back where it preloads out of view.
  const next = useCallback(() => {
    setGrade(null);
    setStack((prev) => {
      if (!prev.length) return prev;
      const rest = prev.slice(1);
      const fresh = drawNext(poolRef.current, rest[rest.length - 1]?.spot);
      return fresh ? [...rest, { key: nextKey.current++, spot: fresh }] : rest;
    });
  }, []);

  // Grade the swiped card against the solver chart. The card has already started
  // flying off; the verdict flashes, then auto-advance rises the next card.
  const onCommit = useCallback(async (action: Action, spot: Spot) => {
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

  const front = stack[0] ?? null;
  const interactive = !!front && !grade && !grading;

  const triggerFront = useCallback(
    (action: Action) => {
      if (!front || grade || grading) return;
      cardRefs.current.get(front.key)?.fling(action);
    },
    [front, grade, grading]
  );

  // Auto-advance after a verdict — no button to press. Wrong answers linger.
  useEffect(() => {
    if (!grade) return;
    const t = setTimeout(next, HOLD_MS[grade.verdict]);
    return () => clearTimeout(t);
  }, [grade, next]);

  // Keyboard parity: ← fold, → open.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'ArrowRight') triggerFront('raise');
      if (e.key === 'ArrowLeft') triggerFront('fold');
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [triggerFront]);

  // Render newest-first so the front card is last in DOM order (paints on top);
  // depth is taken from the logical stack position, not DOM order.
  const rendered = useMemo(() => stack.map((card, depth) => ({ card, depth })).reverse(), [stack]);

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

        {!loading && !error && front && (
          <div className="swd-body">
            <div className="dc-stage">
              {/* The whole deck slides in on the first deal (hides first-card
                  image load); after that, individual cards rise within it. */}
              <motion.div
                className="dc-stack"
                initial={{ y: 70, opacity: 0 }}
                animate={{ y: 0, opacity: 1 }}
                transition={{ type: 'spring', stiffness: 240, damping: 26 }}
              >
                {rendered.map(({ card, depth }) => (
                  <SwipeCard
                    key={card.key}
                    ref={(el) => {
                      if (el) cardRefs.current.set(card.key, el);
                      else cardRefs.current.delete(card.key);
                    }}
                    spot={card.spot}
                    depth={depth}
                    interactive={depth === 0 && interactive}
                    onCommit={onCommit}
                  />
                ))}
              </motion.div>
            </div>

            {/* Verdict flashes then auto-advances; tap it to skip the wait. */}
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
                    onClick={() => triggerFront('fold')}
                    disabled={!interactive}
                  >
                    ← Fold
                  </button>
                  <button
                    className="swd-btn swd-btn--raise"
                    onClick={() => triggerFront('raise')}
                    disabled={!interactive}
                  >
                    Open →
                  </button>
                </div>
              )}
            </div>

            <p className="swd-progress">
              {solid}/{answered} solid · swipe or use ← →
            </p>
          </div>
        )}
      </PageLayout>
    </>
  );
}
