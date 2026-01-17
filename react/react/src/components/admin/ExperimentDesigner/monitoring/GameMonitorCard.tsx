/**
 * GameMonitorCard - Individual game display with poker table visualization
 */

import { Hash, Layers, TrendingUp } from 'lucide-react';
import { MonitoringPokerTable } from './MonitoringPokerTable';
import type { GameSnapshot } from './types';

interface GameMonitorCardProps {
  game: GameSnapshot;
  onPlayerClick: (gameId: string, playerName: string) => void;
}

// Format phase name for display
function formatPhase(phase: string): string {
  return phase.replace(/_/g, ' ').toUpperCase();
}

export function GameMonitorCard({ game, onPlayerClick }: GameMonitorCardProps) {
  const handlePlayerClick = (playerName: string) => {
    onPlayerClick(game.game_id, playerName);
  };

  return (
    <div className="game-monitor-card">
      {/* Header */}
      <div className="game-monitor-card__header">
        <div className="game-monitor-card__id">
          <Hash size={14} />
          <span>{game.game_id.slice(0, 8)}</span>
        </div>
        <div className="game-monitor-card__badges">
          {game.variant && (
            <span className="game-monitor-card__variant-badge">
              {game.variant}
            </span>
          )}
          <span className="game-monitor-card__phase-badge">
            {formatPhase(game.phase)}
          </span>
          <span className="game-monitor-card__hand-badge">
            <Layers size={12} />
            H#{game.hand_number}
          </span>
        </div>
      </div>

      {/* Poker Table */}
      <div className="game-monitor-card__table">
        <MonitoringPokerTable
          players={game.players}
          communityCards={game.community_cards}
          pot={game.pot}
          onPlayerClick={handlePlayerClick}
        />
      </div>

      {/* Footer - Pot display */}
      <div className="game-monitor-card__footer">
        <div className="game-monitor-card__pot">
          <TrendingUp size={14} />
          <span>Pot: ${game.pot.toLocaleString()}</span>
        </div>
      </div>
    </div>
  );
}
