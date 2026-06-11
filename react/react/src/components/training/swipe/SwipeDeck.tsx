import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
  type ReactElement,
  type ReactNode,
  type Ref,
} from 'react';
import { motion, useMotionValue, useTransform, animate, type PanInfo } from 'framer-motion';
import './SwipeDeck.css';

/**
 * SwipeDeck — a reusable Tinder-style card stack for building swipe drills.
 *
 * The hard part (and the reason this is shared): the stack is a persistent ring
 * buffer. Every card is its own element with a STABLE key and its own motion
 * state, positioned by depth. On a swipe the front card flings off and, when you
 * call advance(), the peek behind it RISES into the front slot as the same DOM
 * element — no content swap, no keyed remount, so it can't flash. The swiped card
 * recycles to the back (out of view) and preloads its images there.
 *
 * A drill supplies three things: how to `draw` the next item, how to `renderFace`
 * an item, and what to do `onSwipe`. The deck owns the gesture, the animation, and
 * the buffer; the drill owns the content and the grading/flow.
 *
 * Recommended drill-card anatomy (what `renderFace` should return, top→bottom):
 *   1. situation — the spot/context (position, board, stacks…)
 *   2. your cards — the hero's hand
 * and on the screen *below* the deck the drill renders:
 *   3. stats — small running/result line
 *   4. options — the action controls (buttons mirroring the swipe)
 */

export type SwipeDir = 'left' | 'right' | 'up';

export interface SwipeDeckHandle {
  /** Fling the front card in a direction (button / keyboard parity with a drag).
   *  'up' is only available when `stamps.up` is set. */
  swipe: (dir: SwipeDir) => void;
  /** Drop the front card and rise the next one. Call once you've handled the swipe
   *  (e.g. after a verdict has been shown). */
  advance: () => void;
}

export interface SwipeDeckProps<T> {
  /** Produce the next item, avoiding an immediate repeat of `avoid`. MUST be stable
   *  (memoize it) — the deck rebuilds its stack whenever this identity changes, so
   *  pass a fresh `draw` to reset (e.g. when the pool changes). */
  draw: (avoid: T | null) => T | null;
  /** Render the face content of a card; placed inside the card body. */
  renderFace: (item: T) => ReactNode;
  /** Called when the front card is committed (drag past threshold, or swipe()). */
  onSwipe: (item: T, dir: SwipeDir) => void;
  /** Whether the front card can be dragged right now (disable during grading). */
  interactive?: boolean;
  /** Drag stamp labels. Providing `up` enables a third (upward) swipe direction. */
  stamps?: { left: string; right: string; up?: string };
  /** Cards kept in the ring buffer (front + peeks + hidden preloaders). */
  stackSize?: number;
}

const SWIPE_THRESHOLD = 110; // px past which a release commits the swipe
const FLING_X = 640; // px to fling a committed card off the side
const FLING_Y = 900; // px to fling a committed card off the top (up swipe)

// Resting transform for a card at a given depth. Depth 0 is the front; deeper
// cards sit lower, smaller, and eventually hidden — but still rendered so their
// images preload before they reach the front.
function depthStyle(depth: number) {
  return {
    scale: Math.max(1 - depth * 0.04, 0.84),
    y: depth * 11,
    opacity: depth >= 3 ? 0 : 1,
  };
}

interface CardHandle {
  fling: (dir: SwipeDir) => void;
}

interface CardProps {
  children: ReactNode;
  depth: number;
  interactive: boolean;
  stamps: { left: string; right: string; up?: string };
  stackSize: number;
  onCommit: (dir: SwipeDir) => void;
}

// One card in the stack. Two layers: the outer "slot" carries the depth transform
// (and the stable key, so a depth change animates instead of remounting — the
// peek rises without a flash); the inner "card" carries the drag. Splitting them
// lets the card drag freely (incl. up) without fighting the depth animation. Each
// card owns its own motion values so a recycled card never inherits a stale pose.
const StackCard = forwardRef<CardHandle, CardProps>(function StackCard(
  { children, depth, interactive, stamps, stackSize, onCommit },
  ref
) {
  const x = useMotionValue(0);
  const y = useMotionValue(0);
  const rotate = useTransform(x, [-260, 260], [-11, 11]);
  const rightStamp = useTransform(x, [25, SWIPE_THRESHOLD], [0, 1]);
  const leftStamp = useTransform(x, [-SWIPE_THRESHOLD, -25], [1, 0]);
  const upStamp = useTransform(y, [-SWIPE_THRESHOLD, -25], [1, 0]);
  const allowUp = !!stamps.up;

  const fling = useCallback(
    (dir: SwipeDir) => {
      if (dir === 'up') animate(y, -FLING_Y, { duration: 0.26, ease: 'easeOut' });
      else animate(x, dir === 'right' ? FLING_X : -FLING_X, { duration: 0.26, ease: 'easeOut' });
      onCommit(dir);
    },
    [x, y, onCommit]
  );

  useImperativeHandle(ref, () => ({ fling }), [fling]);

  const onDragEnd = (_e: unknown, info: PanInfo) => {
    const { offset, velocity } = info;
    if (allowUp && offset.y < -SWIPE_THRESHOLD && Math.abs(offset.y) > Math.abs(offset.x)) {
      fling('up');
    } else if (offset.x > SWIPE_THRESHOLD || velocity.x > 500) {
      fling('right');
    } else if (offset.x < -SWIPE_THRESHOLD || velocity.x < -500) {
      fling('left');
    } else {
      animate(x, 0, { type: 'spring', stiffness: 500, damping: 40 });
      animate(y, 0, { type: 'spring', stiffness: 500, damping: 40 });
    }
  };

  const ds = depthStyle(depth);
  return (
    <motion.div
      className="swipedeck-slot"
      style={{ zIndex: stackSize - depth }}
      initial={ds}
      animate={ds}
      transition={{ type: 'spring', stiffness: 300, damping: 30 }}
    >
      <motion.div
        className="swipedeck-card"
        style={{ x, y, rotate }}
        drag={interactive ? (allowUp ? true : 'x') : false}
        dragConstraints={{ left: 0, right: 0, top: 0, bottom: 0 }}
        dragElastic={0.6}
        onDragEnd={interactive ? onDragEnd : undefined}
        whileTap={interactive ? { cursor: 'grabbing' } : undefined}
      >
        {depth === 0 && (
          <>
            <motion.span
              className="swipedeck-stamp swipedeck-stamp--right"
              style={{ opacity: rightStamp }}
            >
              {stamps.right}
            </motion.span>
            <motion.span
              className="swipedeck-stamp swipedeck-stamp--left"
              style={{ opacity: leftStamp }}
            >
              {stamps.left}
            </motion.span>
            {stamps.up && (
              <motion.span
                className="swipedeck-stamp swipedeck-stamp--up"
                style={{ opacity: upStamp }}
              >
                {stamps.up}
              </motion.span>
            )}
          </>
        )}
        <div className="swipedeck-card__body">{children}</div>
      </motion.div>
    </motion.div>
  );
});

interface StackItem<T> {
  key: number;
  item: T;
}

function SwipeDeckInner<T>(
  {
    draw,
    renderFace,
    onSwipe,
    interactive = true,
    stamps = { left: 'NOPE', right: 'YES' },
    stackSize = 5,
  }: SwipeDeckProps<T>,
  ref: Ref<SwipeDeckHandle>
) {
  const nextKey = useRef(0);

  const build = (d: (avoid: T | null) => T | null): StackItem<T>[] => {
    const out: StackItem<T>[] = [];
    let prev: T | null = null;
    for (let i = 0; i < stackSize; i++) {
      const it = d(prev);
      if (it == null) break;
      out.push({ key: nextKey.current++, item: it });
      prev = it;
    }
    return out;
  };

  const [stack, setStack] = useState<StackItem<T>[]>(() => build(draw));

  // Rebuild when `draw` identity changes (e.g. the pool was reloaded). Skips the
  // initial render, which is already seeded by the lazy useState initializer.
  const drawRef = useRef(draw);
  useEffect(() => {
    if (drawRef.current !== draw) {
      drawRef.current = draw;
      setStack(build(draw));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draw]);

  const cardRefs = useRef(new Map<number, CardHandle>());
  const front = stack[0] ?? null;

  const advance = useCallback(() => {
    setStack((prev) => {
      if (!prev.length) return prev;
      const rest = prev.slice(1);
      const fresh = draw(rest[rest.length - 1]?.item ?? prev[0].item);
      return fresh == null ? rest : [...rest, { key: nextKey.current++, item: fresh }];
    });
  }, [draw]);

  const swipe = useCallback(
    (dir: SwipeDir) => {
      if (front) cardRefs.current.get(front.key)?.fling(dir);
    },
    [front]
  );

  useImperativeHandle(ref, () => ({ swipe, advance }), [swipe, advance]);

  // Render newest-first so the front card paints on top; depth comes from the
  // logical stack index, not DOM order.
  const rendered = useMemo(() => stack.map((c, depth) => ({ c, depth })).reverse(), [stack]);

  return (
    <div className="swipedeck-stage">
      {/* The whole deck slides in on mount (hides first-card image load); after
          that, individual cards rise within it. */}
      <motion.div
        className="swipedeck-stack"
        initial={{ y: 70, opacity: 0 }}
        animate={{ y: 0, opacity: 1 }}
        transition={{ type: 'spring', stiffness: 240, damping: 26 }}
      >
        {rendered.map(({ c, depth }) => (
          <StackCard
            key={c.key}
            ref={(el) => {
              if (el) cardRefs.current.set(c.key, el);
              else cardRefs.current.delete(c.key);
            }}
            depth={depth}
            interactive={depth === 0 && interactive}
            stamps={stamps}
            stackSize={stackSize}
            onCommit={(dir) => onSwipe(c.item, dir)}
          >
            {renderFace(c.item)}
          </StackCard>
        ))}
      </motion.div>
    </div>
  );
}

// forwardRef erases the generic; the cast restores the `<T>` call signature.
export const SwipeDeck = forwardRef(SwipeDeckInner) as <T>(
  props: SwipeDeckProps<T> & { ref?: Ref<SwipeDeckHandle> }
) => ReactElement;
