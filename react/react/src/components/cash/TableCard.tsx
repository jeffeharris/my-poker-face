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
import { HandCoins } from 'lucide-react';
import type { LobbyTable } from './types';
import { absolutizeAvatarUrl } from './avatarUrl';
// type-only import keeps the Lobby ↔ TableCard cycle erased at runtime
import type { AiSeatClick } from './Lobby';

interface TableCardProps {
  table: LobbyTable;
  busy: boolean;
  onSeatTap: (seatIndex: number) => void;
  /** Fires when the player clicks an AI portrait — parent opens dossier. */
  onAiSeatClick?: (click: AiSeatClick) => void;
}

export function TableCard({
  table,
  busy,
  onSeatTap,
  onAiSeatClick,
}: TableCardProps) {
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
      <div className="cash-entry__stake-label">
        {table.table_name ?? `${table.stake_label} table`}
      </div>
      <div className="cash-entry__stake-sublabel">
        {table.stake_label}
        {table.table_type && table.table_type !== 'lobby' && (
          <span className={`cash-entry__stake-badge cash-entry__stake-badge--${table.table_type}`}>
            {table.table_type}
          </span>
        )}
      </div>
      <div className="cash-entry__stake-meta">
        BB ${table.big_blind} · min ${table.min_buy_in.toLocaleString()} · max ${table.max_buy_in.toLocaleString()}
      </div>

      <div className="lobby-table-card__roster">
        {table.seats.map((seat) => {
          const isDealer =
            table.dealer_index != null && seat.index === table.dealer_index;
          if (seat.kind === 'ai') {
            const title = seat.relationship_hint
              ? `${seat.name} — ${seat.relationship_hint} (${seat.emotion}). Click for dossier.`
              : `${seat.name} (${seat.emotion}). Click for dossier.`;
            const handleClick = (e: React.MouseEvent<HTMLButtonElement>) => {
              if (!onAiSeatClick) return;
              const rect = (e.currentTarget as HTMLButtonElement).getBoundingClientRect();
              onAiSeatClick({
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
              });
            };
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
                onClick={handleClick}
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
                      <span
                        className="lobby-table-card__seat-initial"
                        aria-hidden="true"
                      >
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
                    <div className="lobby-table-card__seat-hint">
                      {seat.relationship_hint}
                    </div>
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
