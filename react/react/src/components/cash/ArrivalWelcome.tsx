import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Spade } from 'lucide-react';
import { useGameStore } from '../../stores/gameStore';
import { arrivalSubtitle } from '../../utils/arrival';
import './ArrivalWelcome.css';

/** How long the card stays up before it finishes fading out. Matches the
 *  CSS animation duration so the unmount lines up with the fade. */
const DURATION_MS = 2500;

interface Arrival {
  tableId: string;
  tableName: string;
  subtitle: string;
}

/**
 * "You walked into a room" beat on cash-mode sit-down.
 *
 * Watches the seated table id in the store; when it transitions to a new
 * table (fresh sit, resume, or cold-load) we show a brief centered card —
 * room name + a real-clock subtitle ("Murphy's Bar — Tuesday evening") —
 * that fades in and out on its own in ~2.5s. Non-blocking: the live table
 * is clickable behind it (backdrop is pointer-events:none); tapping the
 * card dismisses early. A ref dedupes repeated game-state frames.
 *
 * Mount once per game view (see ResponsiveGameLayout). Renders nothing for
 * tournaments (no cash_mode) or legacy cash sessions without a table name.
 * Portaled to <body> so it can't get trapped under the app header
 * (PageLayout position:fixed stacking trap).
 */
export function ArrivalWelcome() {
  const tableId = useGameStore((s) => s.cashMode?.table_id ?? null);
  const tableName = useGameStore((s) => s.cashMode?.table_name ?? null);
  const announced = useRef<string | null>(null);
  const [arrival, setArrival] = useState<Arrival | null>(null);

  useEffect(() => {
    if (!tableId || !tableName) return;
    if (announced.current === tableId) return;
    announced.current = tableId;
    setArrival({ tableId, tableName, subtitle: arrivalSubtitle() });
    const t = setTimeout(() => setArrival(null), DURATION_MS);
    return () => clearTimeout(t);
  }, [tableId, tableName]);

  if (!arrival) return null;

  return createPortal(
    <div className="arrival-welcome" role="status" aria-live="polite">
      <button
        type="button"
        className="arrival-welcome__card"
        onClick={() => setArrival(null)}
        aria-label={`${arrival.tableName} — ${arrival.subtitle}. Dismiss.`}
      >
        <Spade className="arrival-welcome__icon" size={26} aria-hidden="true" />
        <span className="arrival-welcome__name">{arrival.tableName}</span>
        <span className="arrival-welcome__sub">{arrival.subtitle}</span>
      </button>
    </div>,
    document.body
  );
}
