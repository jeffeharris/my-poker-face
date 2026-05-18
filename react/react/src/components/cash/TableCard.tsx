/**
 * TableCard — one row in the cash lobby.
 *
 * Shows:
 *   - Stake label + buy-in range
 *   - 4-AI roster strip (name + chips + relationship hint icon)
 *   - 2 open-seat tap targets (intent seat + live-fill seat —
 *     UI doesn't distinguish; player taps either)
 *   - Tri-state affordability badge: affordable / sponsor_eligible / locked
 *
 * Each open seat is its own button. Clicking dispatches to
 * `onSeatTap(seatIndex)` which the parent <Lobby> handles
 * (POST /api/cash/sit, navigate on success, open SponsorModal on 402).
 */

import { useCallback } from 'react';
import type { LobbyTable } from './types';

interface TableCardProps {
  table: LobbyTable;
  busy: boolean;
  onSeatTap: (seatIndex: number) => void;
}

export function TableCard({ table, busy, onSeatTap }: TableCardProps) {
  const locked = table.affordability === 'locked';
  const sponsorOnly = table.affordability === 'sponsor_eligible';

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
    [locked, busy, onSeatTap],
  );

  return (
    <div
      className={
        'cash-entry__stake-button' +
        (locked ? ' is-disabled' : '') +
        (sponsorOnly ? ' is-sponsor' : '')
      }
      aria-label={ariaLabel}
    >
      <div className="cash-entry__stake-label">{table.stake_label} table</div>
      <div className="cash-entry__stake-meta">
        BB ${table.big_blind} · min ${table.min_buy_in.toLocaleString()} · max ${table.max_buy_in.toLocaleString()}
      </div>

      <div className="lobby-table-card__roster">
        {table.seats.map((seat) => {
          if (seat.kind === 'ai') {
            const title = seat.relationship_hint
              ? `${seat.name} — ${seat.relationship_hint} (${seat.emotion})`
              : `${seat.name} (${seat.emotion})`;
            return (
              <div
                key={seat.index}
                className={
                  'lobby-table-card__seat lobby-table-card__seat--ai' +
                  ` lobby-table-card__seat--emotion-${seat.emotion}`
                }
                title={title}
                data-emotion={seat.emotion}
              >
                <div className="lobby-table-card__seat-avatar">
                  {seat.avatar_url ? (
                    <img
                      src={seat.avatar_url}
                      alt={seat.name}
                      loading="lazy"
                    />
                  ) : (
                    <span aria-hidden="true">
                      {seat.name.charAt(0).toUpperCase()}
                    </span>
                  )}
                </div>
                <div className="lobby-table-card__seat-name">{seat.name}</div>
                <div className="lobby-table-card__seat-chips">
                  ${seat.chips.toLocaleString()}
                </div>
                {seat.relationship_hint && (
                  <div className="lobby-table-card__seat-hint">
                    {seat.relationship_hint}
                  </div>
                )}
              </div>
            );
          }
          if (seat.kind === 'human') {
            return (
              <div
                key={seat.index}
                className="lobby-table-card__seat lobby-table-card__seat--human"
              >
                <div className="lobby-table-card__seat-name">Seated</div>
                <div className="lobby-table-card__seat-chips">
                  ${seat.chips.toLocaleString()}
                </div>
              </div>
            );
          }
          // open seat
          return (
            <button
              key={seat.index}
              type="button"
              className="lobby-table-card__seat lobby-table-card__seat--open"
              disabled={locked || busy}
              onClick={() => handleSeatClick(seat.index)}
              title={
                locked
                  ? `Earn $${table.min_buy_in.toLocaleString()} to unlock`
                  : sponsorOnly
                    ? 'Sponsor required'
                    : 'Sit here'
              }
            >
              <div className="lobby-table-card__seat-name">Open seat</div>
              <div className="lobby-table-card__seat-cta">
                {locked
                  ? 'Locked'
                  : sponsorOnly
                    ? 'Sponsor'
                    : 'Tap to sit'}
              </div>
            </button>
          );
        })}
      </div>

      {locked && (
        <div className="cash-entry__stake-locked">
          Locked — earn ${table.min_buy_in.toLocaleString()}
        </div>
      )}
      {sponsorOnly && (
        <div className="cash-entry__stake-sponsor">Sponsor required</div>
      )}
    </div>
  );
}
