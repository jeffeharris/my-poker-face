/**
 * CareerHighlightsCard — a tappable card in the cash lobby that previews the
 * player's circuit highlights and opens the full story (`/story`) on click.
 *
 * Pulls a cheap summary from `/api/journey/highlights` (aggregated from the
 * cash_sessions ledger, no hand loading). Renders nothing until there's a
 * story to show, so a brand-new player never sees an empty card.
 */
import { BookOpen, ChevronRight } from 'lucide-react';
import { useEffect, useState } from 'react';

import { config } from '../../config';
import './CareerHighlightsCard.css';

interface Highlights {
  has_story: boolean;
  sessions: number;
  winning_sessions: number;
  total_hands: number;
  biggest_pot: number;
  total_net_chips: number;
  best_session_net: number;
}

interface CareerHighlightsCardProps {
  onOpen: () => void;
}

const signed = (n: number) => `${n > 0 ? '+' : n < 0 ? '−' : ''}${Math.abs(n).toLocaleString()}`;

export function CareerHighlightsCard({ onOpen }: CareerHighlightsCardProps) {
  const [h, setH] = useState<Highlights | null>(null);

  useEffect(() => {
    let alive = true;
    fetch(`${config.API_URL}/api/journey/highlights`, { credentials: 'include' })
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (alive) setH(data);
      })
      .catch(() => {
        /* lobby card is best-effort — stay hidden on error */
      });
    return () => {
      alive = false;
    };
  }, []);

  if (!h?.has_story) return null;

  const netTone = h.total_net_chips > 0 ? 'up' : h.total_net_chips < 0 ? 'down' : 'flat';

  return (
    <button type="button" className="highlights-card" onClick={onOpen}>
      <div className="highlights-card__head">
        <span className="highlights-card__eyebrow">
          <BookOpen size={14} aria-hidden="true" />
          Career highlights
        </span>
        <ChevronRight size={18} className="highlights-card__caret" aria-hidden="true" />
      </div>

      <div className="highlights-card__stats">
        <div className="highlights-card__stat">
          <span className="highlights-card__value">{h.biggest_pot.toLocaleString()}</span>
          <span className="highlights-card__label">Biggest pot</span>
        </div>
        <div className="highlights-card__stat">
          <span className="highlights-card__value">
            {h.winning_sessions}/{h.sessions}
          </span>
          <span className="highlights-card__label">Sessions won</span>
        </div>
        <div className="highlights-card__stat">
          <span className={`highlights-card__value highlights-card__value--${netTone}`}>
            {signed(h.total_net_chips)}
          </span>
          <span className="highlights-card__label">Net chips</span>
        </div>
      </div>

      <span className="highlights-card__cta">Read your circuit story</span>
    </button>
  );
}
