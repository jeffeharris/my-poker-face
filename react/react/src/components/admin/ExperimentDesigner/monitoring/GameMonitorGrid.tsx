/**
 * GameMonitorGrid - CSS Grid layout for multiple game cards
 * Shows ALL games in a single grid (no grouping by variant)
 */

import { GameMonitorCard } from './GameMonitorCard';
import type { GameSnapshot } from './types';

interface GameMonitorGridProps {
  games: GameSnapshot[];
  onPlayerClick: (gameId: string, playerName: string) => void;
}

export function GameMonitorGrid({ games, onPlayerClick }: GameMonitorGridProps) {
  return (
    <div className="game-monitor-grid">
      <div className="game-monitor-grid__cards">
        {games.map((game) => (
          <GameMonitorCard
            key={game.game_id}
            game={game}
            onPlayerClick={onPlayerClick}
          />
        ))}
      </div>
    </div>
  );
}
