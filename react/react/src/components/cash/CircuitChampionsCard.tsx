/**
 * CircuitChampionsCard — the Champions Roll in the cash lobby.
 *
 * The circuit crowns a Main Event winner every cycle whether or not the player
 * sits down, so this is world history, not a personal scorecard. It stays small:
 * collapsed it shows only the LATEST champion; click to expand the recent roll
 * (personas you may never have played, your own titles highlighted, and a quiet
 * marker for the events that ran without you).
 *
 * Self-fetching and best-effort like CareerHighlightsCard: renders nothing until
 * the circuit has crowned at least one champion, so a brand-new player sees no
 * empty shell. Reads the denormalized winner/field-size off `/circuit-history`
 * (no per-row session deserialization).
 */
import { Trophy, Crown, ChevronDown, ChevronUp } from 'lucide-react';
import { useEffect, useState } from 'react';

import { getCircuitHistory, type CircuitChampion } from './tournamentApi';
import { avatarUrlForName } from './avatarUrl';
import { getOrdinal } from '../../types/tournament';
import './CircuitChampionsCard.css';

const MAX_ROWS = 6;

/** Compact "just now / 3h / 2d / 5w" age from an ISO timestamp. */
function ago(iso: string): string {
  const ms = Date.now() - new Date(iso).getTime();
  if (Number.isNaN(ms) || ms < 0) return '';
  const mins = Math.floor(ms / 60_000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins}m`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h`;
  const days = Math.floor(hrs / 24);
  if (days < 7) return `${days}d`;
  return `${Math.floor(days / 7)}w`;
}

function ChampionRow({ event }: { event: CircuitChampion }) {
  const name = event.winner_name ?? 'Unknown';
  const isYou = event.winner_name === 'You';
  const avatar = isYou || !event.winner_name ? null : avatarUrlForName(name);
  const fieldLabel = event.field_size ? `${event.field_size}-player Main Event` : 'Main Event';
  const age = ago(event.completed_at);

  // Right-side tell. Played-and-lost shows your finish; played-and-won is already
  // conveyed by the gold "You" champion row, so it gets no tag; sat-out events
  // carry the quiet "ran without you" — the heart of the living-world theme.
  let tag: { text: string; tone: 'finish' | 'absent' } | null = null;
  if (!event.played) {
    tag = { text: 'ran without you', tone: 'absent' };
  } else if (event.your_finish != null && event.your_finish > 1) {
    tag = { text: `you finished ${getOrdinal(event.your_finish)}`, tone: 'finish' };
  }

  return (
    <div className={`champions-card__row${isYou ? ' champions-card__row--you' : ''}`}>
      <span className="champions-card__avatar" aria-hidden="true">
        {avatar ? (
          <img src={avatar} alt="" loading="lazy" />
        ) : (
          <Crown size={16} className="champions-card__crown" />
        )}
      </span>
      <span className="champions-card__who">
        <span className="champions-card__name">{isYou ? 'You' : name}</span>
        <span className="champions-card__meta">
          {fieldLabel}
          {age ? ` · ${age}` : ''}
        </span>
      </span>
      {tag && (
        <span className={`champions-card__tag champions-card__tag--${tag.tone}`}>{tag.text}</span>
      )}
    </div>
  );
}

export function CircuitChampionsCard() {
  const [events, setEvents] = useState<CircuitChampion[] | null>(null);
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    let alive = true;
    getCircuitHistory()
      .then((data) => {
        if (alive) setEvents(data.events);
      })
      .catch(() => {
        /* lobby card is best-effort — stay hidden on error */
      });
    return () => {
      alive = false;
    };
  }, []);

  if (!events || events.length === 0) return null;

  const hasMore = events.length > 1;
  // Collapsed: just the latest champion. Expanded: the recent roll.
  const shown = expanded ? events.slice(0, MAX_ROWS) : events.slice(0, 1);

  return (
    <section className="champions-card" aria-label="Circuit champions">
      <button
        type="button"
        className="champions-card__toggle"
        onClick={() => setExpanded((v) => !v)}
        aria-expanded={expanded}
        disabled={!hasMore}
      >
        <span className="champions-card__eyebrow">
          <Trophy size={14} aria-hidden="true" />
          {expanded ? 'Circuit champions' : 'Latest champion'}
        </span>
        {hasMore &&
          (expanded ? (
            <ChevronUp size={16} className="champions-card__caret" aria-hidden="true" />
          ) : (
            <ChevronDown size={16} className="champions-card__caret" aria-hidden="true" />
          ))}
      </button>
      <div className="champions-card__list">
        {shown.map((e) => (
          <ChampionRow key={e.tournament_id} event={e} />
        ))}
      </div>
    </section>
  );
}
