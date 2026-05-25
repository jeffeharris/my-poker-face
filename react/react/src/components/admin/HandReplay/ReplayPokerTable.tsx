/**
 * ReplayPokerTable - Poker table visualization for hand replay
 *
 * Uses ellipse-based positioning like MonitoringPokerTable.
 * Displays community cards, pot, phase badge, and player slots.
 */

import { memo, useMemo } from 'react';
import { Card } from '../../cards/Card';
import { ReplayPlayerSlot } from './ReplayPlayerSlot';
import type { VisualState } from './types';

interface ReplayPokerTableProps {
  visualState: VisualState;
}

export const ReplayPokerTable = memo(function ReplayPokerTable({
  visualState,
}: ReplayPokerTableProps) {
  const totalSeats = visualState.players.length;

  const getSeatStyle = useMemo(() => {
    return (seatIndex: number) => {
      const startAngle = 90;
      const angleStep = 360 / totalSeats;
      const angle = (startAngle + seatIndex * angleStep) * (Math.PI / 180);

      const radiusX = 42;
      const radiusY = 38;

      const left = 50 + radiusX * Math.cos(angle);
      const top = 50 + radiusY * Math.sin(angle);

      return {
        position: 'absolute' as const,
        left: `${left}%`,
        top: `${top}%`,
        transform: 'translate(-50%, -50%)',
      };
    };
  }, [totalSeats]);

  return (
    <div className="replay-table">
      {/* Table felt */}
      <div className="replay-table__felt">
        {/* Phase badge */}
        <div className="replay-table__phase-badge">{visualState.phase.replace('_', ' ')}</div>

        {/* Community cards */}
        <div className="replay-table__community">
          {visualState.community_cards.length > 0 ? (
            visualState.community_cards.map((card, i) => (
              <Card
                key={i}
                card={card}
                faceDown={false}
                size="small"
                className="replay-table__community-card"
              />
            ))
          ) : (
            <div className="replay-table__no-cards">
              <span className="replay-table__waiting">Pre-Flop</span>
            </div>
          )}
        </div>

        {/* Pot display */}
        {visualState.pot > 0 && (
          <div className="replay-table__pot">
            <span className="replay-table__pot-label">Pot</span>
            <span className="replay-table__pot-amount">${visualState.pot.toLocaleString()}</span>
          </div>
        )}
      </div>

      {/* Player slots */}
      <div className="replay-table__players">
        {visualState.players.map((player) => (
          <div
            key={player.name}
            style={getSeatStyle(player.seat_index)}
            className="replay-table__player-wrapper"
          >
            <ReplayPlayerSlot player={player} />
          </div>
        ))}
      </div>
    </div>
  );
});
