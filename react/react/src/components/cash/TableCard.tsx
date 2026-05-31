/**
 * TableCard — one table in the cash lobby (Cardroom or Casino tab).
 *
 * Compact by default: header (name + buy-in), a Sit affordance, a
 * scouting line (open seats · most chips · fish/whale · you owe), and a
 * condensed avatar-pip strip. Tapping "show table" expands to the full
 * 6-seat portrait roster (the seats you can tap to sit, plus AI portraits
 * that open a dossier). Casino tables can also show a "closing" countdown.
 *
 * The scouting derivations are pure functions of `table.seats`, so the
 * same card serves both venues — fish/whale tags simply don't appear on
 * Cardroom tables (fish are casino-only).
 */

import { useCallback, useState } from 'react';
import {
  HandCoins,
  Coins,
  Fish,
  Wallet,
  Clock,
  MapPin,
  ChevronDown,
  ChevronUp,
} from 'lucide-react';
import type { LobbySeat, LobbyTable } from './types';
import { absolutizeAvatarUrl } from './avatarUrl';
// type-only import keeps the Lobby ↔ TableCard cycle erased at runtime
import type { AiSeatClick } from './Lobby';

interface TableCardProps {
  table: LobbyTable;
  busy: boolean;
  onSeatTap: (seatIndex: number) => void;
  /** Fires when the player clicks an AI portrait — parent opens dossier. */
  onAiSeatClick?: (click: AiSeatClick) => void;
  /** True when this is the table the player is currently seated at. Flips
   *  the card to a "you're here" pin: Sit becomes Resume. */
  isSeated?: boolean;
  /** Resume the in-progress game on this table. Required to do anything
   *  useful when `isSeated`. */
  onResume?: () => void;
  /** Spotlight glow — Sal's post-graduation handoff points the player here. */
  spotlight?: boolean;
}

/** "$4.2k" / "$980" — compact chip count for the scouting line. */
function shortChips(n: number): string {
  if (n >= 1000) {
    const k = n / 1000;
    return `$${k >= 10 ? Math.round(k) : k.toFixed(1)}k`;
  }
  return `$${n}`;
}

function seatChips(seat: LobbySeat): number {
  return seat.kind === 'ai' || seat.kind === 'human' ? seat.chips : 0;
}

export function TableCard({
  table,
  busy,
  onSeatTap,
  onAiSeatClick,
  isSeated = false,
  onResume,
  spotlight = false,
}: TableCardProps) {
  const [expanded, setExpanded] = useState(false);

  const locked = table.affordability === 'locked';
  const sponsorOnly = table.affordability === 'sponsor_eligible';
  const closing = table.closing_hand_countdown != null;

  // --- scouting derivations (pure over seats) ---
  const openSeats = table.seats.filter((s) => s.kind === 'open');
  const occupied = table.seats.filter((s) => s.kind === 'ai' || s.kind === 'human');
  const openCount = openSeats.length;
  const firstOpenIndex = openSeats[0]?.index;
  const mostChips = occupied.reduce((m, s) => Math.max(m, seatChips(s)), 0);
  const whaleSeat = table.seats.find((s) => s.kind === 'ai' && s.role === 'whale');
  const fishOnlyCount = table.seats.filter((s) => s.kind === 'ai' && s.role === 'fish').length;
  const carryTotal = table.seats.reduce(
    (sum, s) => sum + (s.kind === 'ai' ? (s.carry_amount ?? 0) : 0),
    0
  );

  const ariaLabel = locked
    ? `${table.stake_label} table — locked, earn $${table.min_buy_in.toLocaleString()} to unlock`
    : sponsorOnly
      ? `${table.stake_label} table — sponsor required`
      : `${table.stake_label} table`;

  const handleSeatClick = useCallback(
    (seatIndex: number) => {
      if (locked || busy) return;
      onSeatTap(seatIndex);
    },
    [locked, busy, onSeatTap]
  );

  const buildDossierClick = useCallback(
    (seat: Extract<LobbySeat, { kind: 'ai' }>, el: HTMLElement): AiSeatClick => {
      const rect = el.getBoundingClientRect();
      return {
        dossier: {
          name: seat.name,
          avatarUrl: absolutizeAvatarUrl(seat.avatar_url) ?? undefined,
          emotion: seat.emotion,
          chips: { atTable: seat.chips },
          affiliation: seat.relationship_hint
            ? {
                relationship: 'neutral',
                relationshipNote: seat.relationship_hint,
              }
            : undefined,
        },
        origin: { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 },
        identifier: seat.personality_id,
      };
    },
    []
  );

  // Sit button: taps the first open seat. Disabled when locked/busy/full.
  const canSit = !locked && !busy && firstOpenIndex !== undefined;
  const sitLabel = locked ? 'Locked' : sponsorOnly ? 'Sponsor' : openCount === 0 ? 'Full' : 'Sit';

  return (
    <div
      className={
        'lobby-table-card' +
        (locked ? ' is-disabled' : '') +
        (sponsorOnly ? ' is-sponsor' : '') +
        (closing ? ' is-closing' : '') +
        (isSeated ? ' is-seated' : '') +
        (spotlight ? ' is-spotlight' : '')
      }
      aria-label={isSeated ? `${ariaLabel} — you're seated here` : ariaLabel}
    >
      <div className="lobby-table-card__head">
        <div className="lobby-table-card__title">
          <div className="lobby-table-card__name">
            {table.table_name ?? `${table.stake_label} table`}
            {isSeated && (
              <span className="lobby-table-card__here" title="You're seated at this table">
                <MapPin size={11} aria-hidden="true" />
                You're here
              </span>
            )}
            {closing && (
              <span className="lobby-table-card__closing" title="This table is breaking up">
                <Clock size={11} aria-hidden="true" />
                closing · {table.closing_hand_countdown}{' '}
                {table.closing_hand_countdown === 1 ? 'hand' : 'hands'}
              </span>
            )}
          </div>
          <div className="lobby-table-card__meta">
            {table.stake_label} · BB ${table.big_blind} · buy-in $
            {table.min_buy_in.toLocaleString()}–${table.max_buy_in.toLocaleString()}
          </div>
        </div>
        {isSeated ? (
          <button
            type="button"
            className="lobby-table-card__sit lobby-table-card__sit--resume"
            onClick={() => onResume?.()}
            title="Return to your game at this table"
          >
            Resume
          </button>
        ) : (
          <button
            type="button"
            className="lobby-table-card__sit"
            disabled={!canSit}
            onClick={() => firstOpenIndex !== undefined && handleSeatClick(firstOpenIndex)}
            title={
              locked
                ? `Earn $${table.min_buy_in.toLocaleString()} to unlock`
                : sponsorOnly
                  ? 'Sponsor required to sit'
                  : openCount === 0
                    ? 'No open seats'
                    : `Sit — buy in $${table.min_buy_in.toLocaleString()}`
            }
          >
            {sitLabel}
            {canSit && <small>buy-in ${table.min_buy_in.toLocaleString()}</small>}
          </button>
        )}
      </div>

      {/* Scouting line — the decision drivers at a glance. */}
      <div className="lobby-table-card__scout">
        <span className={'lobby-table-card__scout-open' + (openCount === 0 ? ' is-full' : '')}>
          <i aria-hidden="true" />
          {openCount > 0 ? `${openCount} open` : 'full'}
        </span>
        {whaleSeat && whaleSeat.kind === 'ai' && (
          <span className="lobby-table-card__scout-whale">
            <Fish size={13} aria-hidden="true" />
            whale on {shortChips(whaleSeat.chips)}
          </span>
        )}
        {fishOnlyCount > 0 && (
          <span className="lobby-table-card__scout-fish">
            <Fish size={13} aria-hidden="true" />
            {fishOnlyCount} fish
          </span>
        )}
        {!whaleSeat && mostChips > 0 && (
          <span className="lobby-table-card__scout-chips">
            <Coins size={13} aria-hidden="true" />
            most chips {shortChips(mostChips)}
          </span>
        )}
        {carryTotal > 0 && (
          <span className="lobby-table-card__scout-owe">
            <Wallet size={13} aria-hidden="true" />
            you owe ${carryTotal.toLocaleString()}
          </span>
        )}
      </div>

      {!expanded ? (
        // Compact: condensed avatar-pip strip + expander.
        <div className="lobby-table-card__pips">
          {occupied.map((seat) =>
            seat.kind === 'ai' ? (
              <button
                key={seat.index}
                type="button"
                className={`lobby-table-card__pip lobby-table-card__pip--emotion-${seat.emotion}`}
                title={`${seat.name} — ${seat.emotion}. Click for dossier.`}
                onClick={(e) => onAiSeatClick?.(buildDossierClick(seat, e.currentTarget))}
                aria-label={`Open dossier for ${seat.name}`}
              >
                {(() => {
                  const src = absolutizeAvatarUrl(seat.avatar_url);
                  return src ? (
                    <img src={src} alt={seat.name} loading="lazy" />
                  ) : (
                    <span aria-hidden="true">{seat.name.charAt(0).toUpperCase()}</span>
                  );
                })()}
              </button>
            ) : (
              <span
                key={seat.index}
                className="lobby-table-card__pip lobby-table-card__pip--human"
                title="Seated player"
                aria-hidden="true"
              >
                ♟
              </span>
            )
          )}
          {openCount > 0 && (
            <span className="lobby-table-card__pip lobby-table-card__pip--open" aria-hidden="true">
              +{openCount}
            </span>
          )}
          <span className="lobby-table-card__pips-rest" />
          <button
            type="button"
            className="lobby-table-card__expander"
            onClick={() => setExpanded(true)}
            aria-expanded={false}
          >
            show table <ChevronDown size={14} aria-hidden="true" />
          </button>
        </div>
      ) : (
        // Expanded: full portrait roster.
        <>
          <div className="lobby-table-card__roster">
            {table.seats.map((seat) => {
              const isDealer = table.dealer_index != null && seat.index === table.dealer_index;
              if (seat.kind === 'ai') {
                const title = seat.relationship_hint
                  ? `${seat.name} — ${seat.relationship_hint} (${seat.emotion}). Click for dossier.`
                  : `${seat.name} (${seat.emotion}). Click for dossier.`;
                return (
                  <button
                    key={seat.index}
                    type="button"
                    className={
                      'lobby-table-card__seat lobby-table-card__seat--ai' +
                      ` lobby-table-card__seat--emotion-${seat.emotion}` +
                      ' lobby-table-card__seat--clickable'
                    }
                    title={title}
                    data-emotion={seat.emotion}
                    onClick={(e) => onAiSeatClick?.(buildDossierClick(seat, e.currentTarget))}
                    aria-label={`Open dossier for ${seat.name}`}
                  >
                    {isDealer && (
                      <span
                        className="lobby-table-card__dealer-button"
                        title="Dealer button"
                        aria-label="Dealer"
                      >
                        D
                      </span>
                    )}
                    {seat.role && (
                      <span
                        className={`lobby-table-card__role lobby-table-card__role--${seat.role}`}
                        title={
                          seat.role === 'whale'
                            ? 'Whale — loose, passive, and deep-pocketed'
                            : 'Fish — loose, passive donor'
                        }
                      >
                        {seat.role}
                      </span>
                    )}
                    {seat.carry_amount !== undefined && (
                      <span
                        className="lobby-table-card__carry-pin"
                        title={`You owe ${seat.name} $${seat.carry_amount.toLocaleString()}`}
                        aria-label={`You owe ${seat.name} ${seat.carry_amount} chips`}
                      >
                        ${seat.carry_amount.toLocaleString()}
                      </span>
                    )}
                    {seat.in_active_stake && (
                      <span
                        className="lobby-table-card__stake-glyph"
                        title={`${seat.name} is currently in an active stake`}
                        aria-label={`${seat.name} is currently in an active stake position`}
                      >
                        <HandCoins size={10} aria-hidden="true" />
                      </span>
                    )}
                    <div className="lobby-table-card__seat-image">
                      {(() => {
                        const src = absolutizeAvatarUrl(seat.avatar_url);
                        return src ? (
                          <img src={src} alt={seat.name} loading="lazy" />
                        ) : (
                          <span className="lobby-table-card__seat-initial" aria-hidden="true">
                            {seat.name.charAt(0).toUpperCase()}
                          </span>
                        );
                      })()}
                    </div>
                    <div className="lobby-table-card__seat-overlay">
                      <div className="lobby-table-card__seat-name">{seat.name}</div>
                      <div className="lobby-table-card__seat-chips">
                        ${seat.chips.toLocaleString()}
                      </div>
                      {seat.relationship_hint && (
                        <div className="lobby-table-card__seat-hint">{seat.relationship_hint}</div>
                      )}
                    </div>
                  </button>
                );
              }
              if (seat.kind === 'human') {
                return (
                  <div
                    key={seat.index}
                    className="lobby-table-card__seat lobby-table-card__seat--human"
                  >
                    {isDealer && (
                      <span
                        className="lobby-table-card__dealer-button"
                        title="Dealer button"
                        aria-label="Dealer"
                      >
                        D
                      </span>
                    )}
                    <div className="lobby-table-card__seat-name">Seated</div>
                    <div className="lobby-table-card__seat-chips">
                      ${seat.chips.toLocaleString()}
                    </div>
                  </div>
                );
              }
              // open seat. On the table you're already seated at, an open
              // seat can't be sat (you can't sit twice) — tapping it resumes
              // your game instead of starting a second session.
              return (
                <button
                  key={seat.index}
                  type="button"
                  className="lobby-table-card__seat lobby-table-card__seat--open"
                  disabled={!isSeated && (locked || busy)}
                  onClick={() => (isSeated ? onResume?.() : handleSeatClick(seat.index))}
                  title={
                    isSeated
                      ? 'Resume your game at this table'
                      : locked
                        ? `Earn $${table.min_buy_in.toLocaleString()} to unlock`
                        : sponsorOnly
                          ? 'Sponsor required'
                          : 'Sit here'
                  }
                >
                  <div className="lobby-table-card__seat-name">Open seat</div>
                  <div className="lobby-table-card__seat-cta">
                    {isSeated
                      ? 'Resume'
                      : locked
                        ? 'Locked'
                        : sponsorOnly
                          ? 'Sponsor'
                          : 'Tap to sit'}
                  </div>
                </button>
              );
            })}
          </div>
          <div className="lobby-table-card__pips lobby-table-card__pips--footer">
            <span className="lobby-table-card__pips-rest" />
            <button
              type="button"
              className="lobby-table-card__expander"
              onClick={() => setExpanded(false)}
              aria-expanded
            >
              hide <ChevronUp size={14} aria-hidden="true" />
            </button>
          </div>
        </>
      )}

      {locked && (
        <div className="cash-entry__stake-locked">
          Locked — earn ${table.min_buy_in.toLocaleString()}
        </div>
      )}
      {sponsorOnly && <div className="cash-entry__stake-sponsor">Sponsor required</div>}
    </div>
  );
}
