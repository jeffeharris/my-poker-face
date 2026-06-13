/**
 * CircuitChampionsCard — the Champions Roll in the cash lobby.
 *
 * The circuit crowns a Main Event winner every cycle whether or not the player
 * sits down, so this is world history, not a personal scorecard: it lists recent
 * champions (personas you may never have played), with the player's own titles
 * highlighted and a quiet marker for the events that ran without them. Reinforces
 * that the personas have careers accruing independent of the player.
 *
 * Self-fetching and best-effort like CareerHighlightsCard: renders nothing until
 * the circuit has crowned at least one champion, so a brand-new player sees no
 * empty shell. Reads the denormalized winner/field-size off `/circuit-history`
 * (no per-row session deserialization).
 */
import { Trophy, Crown } from 'lucide-react';
import { useEffect, useState } from 'react';

import { getCircuitHistory, type CircuitChampion } from './tournamentApi';
import { avatarUrlForName } from './avatarUrl';
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

  return (
    <li className={`champions-card__row${isYou ? ' champions-card__row--you' : ''}`}>
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
      {/* The quiet "ran without you" tell — the heart of the living-world theme. */}
      {!event.played && <span className="champions-card__tag">ran without you</span>}
    </li>
  );
}

export function CircuitChampionsCard() {
  const [events, setEvents] = useState<CircuitChampion[] | null>(null);

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

  return (
    <section className="champions-card" aria-label="Circuit champions">
      <div className="champions-card__head">
        <span className="champions-card__eyebrow">
          <Trophy size={14} aria-hidden="true" />
          Circuit champions
        </span>
      </div>
      <ul className="champions-card__list">
        {events.slice(0, MAX_ROWS).map((e) => (
          <ChampionRow key={e.tournament_id} event={e} />
        ))}
      </ul>
    </section>
  );
}
