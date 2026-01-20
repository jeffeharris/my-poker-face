/**
 * MonitoringPokerTable - Simplified poker table for monitoring view
 *
 * Uses ellipse-based positioning similar to the main PokerTable component,
 * but in a more compact form suitable for the multi-game grid.
 */

import { MonitoringPlayerSlot } from './MonitoringPlayerSlot';
import { Card } from '../../../cards/Card';
import type { MonitoringPlayer, MonitoringCard } from './types';

interface MonitoringPokerTableProps {
  players: MonitoringPlayer[];
  communityCards: MonitoringCard[];
  pot: number;
  onPlayerClick: (playerName: string) => void;
}

export function MonitoringPokerTable({
  players,
  communityCards,
  pot,
  onPlayerClick,
}: MonitoringPokerTableProps) {
  // Calculate seat position around the table based on player count
  // Positions are distributed clockwise starting from bottom
  const getSeatStyle = (seatIndex: number, totalPlayers: number) => {
    const startAngle = 90; // Bottom center (in degrees)
    const angleStep = 360 / totalPlayers;
    const angle = (startAngle + seatIndex * angleStep) * (Math.PI / 180);

    // Ellipse radii for compact table
    const radiusX = 42; // Horizontal radius as percentage
    const radiusY = 38; // Vertical radius as percentage

    const left = 50 + radiusX * Math.cos(angle);
    const top = 50 + radiusY * Math.sin(angle);

    return {
      position: 'absolute' as const,
      left: `${left}%`,
      top: `${top}%`,
      transform: 'translate(-50%, -50%)',
    };
  };

  return (
    <div className="monitoring-table">
      {/* Table felt */}
      <div className="monitoring-table__felt">
        {/* Community cards area */}
        <div className="monitoring-table__community">
          {communityCards.length > 0 ? (
            communityCards.map((card, i) => (
              <Card
                key={i}
                card={card}
                faceDown={false}
                size="small"
                className="monitoring-table__community-card"
              />
            ))
          ) : (
            <div className="monitoring-table__no-cards">
              {/* Placeholder for pre-flop */}
              <span className="monitoring-table__waiting">Waiting...</span>
            </div>
          )}
        </div>

        {/* Pot display */}
        {pot > 0 && (
          <div className="monitoring-table__pot">
            <span className="monitoring-table__pot-label">Pot</span>
            <span className="monitoring-table__pot-amount">${pot.toLocaleString()}</span>
          </div>
        )}
      </div>

      {/* Player slots positioned around the table */}
      <div className="monitoring-table__players">
        {players.map((player, index) => (
          <div
            key={player.name}
            style={getSeatStyle(index, players.length)}
            className="monitoring-table__player-wrapper"
          >
            <MonitoringPlayerSlot
              player={player}
              onClick={() => onPlayerClick(player.name)}
            />
          </div>
        ))}
      </div>
    </div>
  );
}
