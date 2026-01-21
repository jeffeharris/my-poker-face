/**
 * MonitoringPlayerSlot - Compact player display for monitoring grid
 *
 * Shows player name, stack, cards, and status indicators.
 * Clickable to open drill-down panel.
 */

import { Card } from '../../../cards/Card';
import type { MonitoringPlayer } from './types';

interface MonitoringPlayerSlotProps {
  player: MonitoringPlayer;
  onClick: () => void;
}

export function MonitoringPlayerSlot({ player, onClick }: MonitoringPlayerSlotProps) {
  const statusClass = [
    player.is_eliminated && 'monitoring-player--eliminated',
    player.is_folded && !player.is_eliminated && 'monitoring-player--folded',
    player.is_all_in && 'monitoring-player--all-in',
    player.is_current && 'monitoring-player--current',
  ].filter(Boolean).join(' ');

  // Check if player has tilt (moderate or higher)
  // Use optional chaining since psychology may be disabled for some experiments
  const hasTilt = (player.psychology?.tilt_level ?? 0) >= 40;

  // Eliminated players show a simplified "out" state
  if (player.is_eliminated) {
    return (
      <button
        className={`monitoring-player ${statusClass}`}
        onClick={onClick}
        type="button"
        title={`${player.name} - Eliminated`}
      >
        <div className="monitoring-player__info">
          <span className="monitoring-player__name">{player.name}</span>
        </div>
        <div className="monitoring-player__badges">
          <span className="monitoring-player__badge monitoring-player__badge--eliminated">
            OUT
          </span>
        </div>
      </button>
    );
  }

  return (
    <button
      className={`monitoring-player ${statusClass}`}
      onClick={onClick}
      type="button"
      title={`Click for ${player.name} details`}
    >
      {/* Player info */}
      <div className="monitoring-player__info">
        <span className="monitoring-player__name">{player.name}</span>
        <span className="monitoring-player__stack">${player.stack.toLocaleString()}</span>
      </div>

      {/* Hole cards - always visible in monitoring mode */}
      <div className="monitoring-player__cards">
        {player.hole_cards && player.hole_cards.length >= 2 ? (
          <>
            <Card
              card={player.hole_cards[0]}
              faceDown={false}
              size="small"
              className="monitoring-player__card"
            />
            <Card
              card={player.hole_cards[1]}
              faceDown={false}
              size="small"
              className="monitoring-player__card"
            />
          </>
        ) : (
          <>
            <Card faceDown={true} size="small" className="monitoring-player__card" />
            <Card faceDown={true} size="small" className="monitoring-player__card" />
          </>
        )}
      </div>

      {/* Status badges */}
      <div className="monitoring-player__badges">
        {player.is_folded && (
          <span className="monitoring-player__badge monitoring-player__badge--folded">
            FOLDED
          </span>
        )}
        {player.is_all_in && (
          <span className="monitoring-player__badge monitoring-player__badge--all-in">
            ALL-IN
          </span>
        )}
        {player.is_current && !player.is_folded && (
          <span className="monitoring-player__badge monitoring-player__badge--current">
            TURN
          </span>
        )}
        {hasTilt && (
          <span className="monitoring-player__badge monitoring-player__badge--tilt">
            TILT
          </span>
        )}
      </div>

      {/* Bet indicator */}
      {player.bet > 0 && (
        <div className="monitoring-player__bet">
          ${player.bet.toLocaleString()}
        </div>
      )}
    </button>
  );
}
