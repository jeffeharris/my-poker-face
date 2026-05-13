/**
 * ReplayPlayerSlot - Player display for hand replay table
 *
 * Shows player name, stack, hole cards, action badge, and state indicators.
 * Forked from MonitoringPlayerSlot with replay-specific features.
 */

import { memo } from 'react';
import { Card } from '../../cards/Card';
import type { VisualPlayer } from './types';

interface ReplayPlayerSlotProps {
  player: VisualPlayer;
}

const ACTION_CLASS_MAP: Record<string, string> = {
  fold: 'action-fold',
  check: 'action-check',
  call: 'action-call',
  raise: 'action-raise',
  bet: 'action-bet',
  all_in: 'action-all_in',
  post_blind: 'action-call',
};

export const ReplayPlayerSlot = memo(function ReplayPlayerSlot({ player }: ReplayPlayerSlotProps) {
  const statusClass = [
    'replay-player',
    player.is_folded && 'replay-player--folded',
    player.is_all_in && 'replay-player--all-in',
    player.is_current && 'replay-player--current',
  ].filter(Boolean).join(' ');

  return (
    <div className={statusClass}>
      {/* Player info */}
      <div className="replay-player__info">
        <span className="replay-player__name">{player.name}</span>
        <span className="replay-player__position">{player.position}</span>
        <span className="replay-player__stack">${player.stack.toLocaleString()}</span>
      </div>

      {/* Hole cards */}
      <div className="replay-player__cards">
        {player.hole_cards && player.hole_cards.length >= 2 ? (
          <>
            <Card card={player.hole_cards[0]} faceDown={false} size="small" className="replay-player__card" />
            <Card card={player.hole_cards[1]} faceDown={false} size="small" className="replay-player__card" />
          </>
        ) : (
          <>
            <Card faceDown={true} size="small" className="replay-player__card" />
            <Card faceDown={true} size="small" className="replay-player__card" />
          </>
        )}
      </div>

      {/* Action badge */}
      {player.last_action && (
        <span className={`replay-player__action action-badge ${ACTION_CLASS_MAP[player.last_action] ?? ''}`}>
          {player.last_action.toUpperCase()}
        </span>
      )}

      {/* Bet indicator */}
      {player.bet > 0 && (
        <div className="replay-player__bet">
          ${player.bet.toLocaleString()}
        </div>
      )}
    </div>
  );
});
