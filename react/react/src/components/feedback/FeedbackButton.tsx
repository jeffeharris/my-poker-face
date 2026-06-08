import { createPortal } from 'react-dom';
import { useCallback, useRef, useState, type CSSProperties, type PointerEvent } from 'react';
import { useLocation } from 'react-router-dom';
import { MessageSquareWarning } from 'lucide-react';
import { sentryEnabled, openFeedbackForm } from '../../sentry';
import { useAuth } from '../../hooks/useAuth';
import { logger } from '../../utils/logger';
import './FeedbackButton.css';

const STORAGE_KEY = 'feedback_button_pos';
const DRAG_THRESHOLD = 8; // px of movement before a press counts as a drag, not a tap
const BTN = 44; // launcher size, for edge clamping/snapping

type Pos = { x: number; y: number }; // x = left offset, y = bottom offset (px)

function loadPos(): Pos | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) return JSON.parse(raw) as Pos;
  } catch {
    /* ignore */
  }
  return null;
}

/**
 * Routes that show a poker action bar (Fold/Call/Raise) at the bottom, where the
 * launcher must sit higher so it never overlaps the controls. Used only for the
 * default position — a user-dragged position overrides it.
 */
function isInGame(pathname: string): boolean {
  return (
    (pathname.startsWith('/game/') && !pathname.startsWith('/game/new')) ||
    pathname === '/cash' ||
    pathname.startsWith('/tournament')
  );
}

/**
 * App-wide "report a bug" launcher. Floats over the authenticated app and, on
 * tap, opens the Sentry user-feedback form (which auto-attaches the active
 * session replay plus console/network breadcrumbs and our user/game context).
 *
 * Draggable like the coach puck: press-and-drag repositions it, snapping to the
 * nearer horizontal edge and persisting to localStorage; a plain tap opens the
 * form. Until dragged it uses the route-aware default position (bottom-left,
 * raised in-game). Renders nothing when Sentry is disabled or signed-out.
 * Portaled to <body> so the fixed launcher escapes ancestor stacking contexts.
 */
export function FeedbackButton() {
  const { isAuthenticated } = useAuth();
  const { pathname } = useLocation();
  // Null until the user drags it; then a persisted custom position takes over.
  const [pos, setPos] = useState<Pos | null>(loadPos);
  const btnRef = useRef<HTMLButtonElement | null>(null);
  const drag = useRef({ startX: 0, startY: 0, baseX: 0, baseY: 0, moved: 0, dragging: false });
  const suppressClick = useRef(false);

  const onPointerDown = useCallback(
    (e: PointerEvent<HTMLButtonElement>) => {
      const el = btnRef.current;
      if (!el) return;
      el.setPointerCapture(e.pointerId);
      // Seed the drag from the saved position, or from the current rendered rect
      // when it's still at its CSS default (the first drag).
      let baseX = pos?.x ?? 0;
      let baseY = pos?.y ?? 0;
      if (!pos) {
        const r = el.getBoundingClientRect();
        baseX = r.left;
        baseY = window.innerHeight - r.bottom;
      }
      drag.current = {
        startX: e.clientX,
        startY: e.clientY,
        baseX,
        baseY,
        moved: 0,
        dragging: false,
      };
    },
    [pos]
  );

  const onPointerMove = useCallback((e: PointerEvent<HTMLButtonElement>) => {
    const el = btnRef.current;
    if (!el || !el.hasPointerCapture(e.pointerId)) return; // only while pressed
    const d = drag.current;
    const dx = e.clientX - d.startX;
    const dy = e.clientY - d.startY;
    d.moved = Math.abs(dx) + Math.abs(dy);
    if (d.moved <= DRAG_THRESHOLD) return;
    d.dragging = true;
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    setPos({
      x: Math.max(8, Math.min(vw - BTN - 8, d.baseX + dx)),
      y: Math.max(8, Math.min(vh - BTN - 8, d.baseY - dy)),
    });
  }, []);

  const onPointerUp = useCallback((e: PointerEvent<HTMLButtonElement>) => {
    try {
      btnRef.current?.releasePointerCapture(e.pointerId);
    } catch {
      /* ignore */
    }
    if (!drag.current.dragging) return; // a tap — let onClick open the form
    suppressClick.current = true; // it was a drag; swallow the trailing click
    const vw = window.innerWidth;
    setPos((prev) => {
      if (!prev) return prev;
      // Snap to the nearer horizontal edge, keep the vertical position.
      const center = prev.x + BTN / 2;
      const snapped = { x: center < vw / 2 ? 16 : vw - BTN - 16, y: prev.y };
      try {
        localStorage.setItem(STORAGE_KEY, JSON.stringify(snapped));
      } catch {
        /* ignore */
      }
      return snapped;
    });
  }, []);

  const onClick = useCallback(() => {
    if (suppressClick.current) {
      suppressClick.current = false; // tail of a drag, not a real click
      return;
    }
    openFeedbackForm().catch((err) => logger.error('[feedback] failed to open form', err));
  }, []);

  if (!sentryEnabled || !isAuthenticated) return null;

  const style: CSSProperties = pos
    ? { left: pos.x, bottom: pos.y, right: 'auto', top: 'auto' }
    : {};
  const className = `feedback-button${!pos && isInGame(pathname) ? ' feedback-button--in-game' : ''}`;

  return createPortal(
    <button
      ref={btnRef}
      type="button"
      className={className}
      style={style}
      aria-label="Report a bug or send feedback"
      title="Report a bug or send feedback (drag to move)"
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onClick={onClick}
    >
      <MessageSquareWarning size={20} aria-hidden="true" />
    </button>,
    document.body
  );
}
