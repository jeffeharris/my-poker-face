/**
 * GameMonitorGrid - CSS Grid layout for multiple game cards
 */

import { GameMonitorCard } from './GameMonitorCard';
import type { GameSnapshot } from './types';

interface GameMonitorGridProps {
  games: GameSnapshot[];
  onPlayerClick: (gameId: string, playerName: string) => void;
}

export function GameMonitorGrid({ games, onPlayerClick }: GameMonitorGridProps) {
  // Group games by variant for visual organization
  const gamesByVariant = games.reduce((acc, game) => {
    const key = game.variant || 'default';
    if (!acc[key]) {
      acc[key] = [];
    }
    acc[key].push(game);
    return acc;
  }, {} as Record<string, GameSnapshot[]>);

  const hasVariants = Object.keys(gamesByVariant).length > 1 ||
    (Object.keys(gamesByVariant).length === 1 && !gamesByVariant['default']);

  return (
    <div className="game-monitor-grid">
      {hasVariants ? (
        // Render grouped by variant
        Object.entries(gamesByVariant).map(([variant, variantGames]) => (
          <div key={variant} className="game-monitor-grid__variant-group">
            <h3 className="game-monitor-grid__variant-label">{variant}</h3>
            <div className="game-monitor-grid__cards">
              {variantGames.map((game) => (
                <GameMonitorCard
                  key={game.game_id}
                  game={game}
                  onPlayerClick={onPlayerClick}
                />
              ))}
            </div>
          </div>
        ))
      ) : (
        // Render flat grid
        <div className="game-monitor-grid__cards">
          {games.map((game) => (
            <GameMonitorCard
              key={game.game_id}
              game={game}
              onPlayerClick={onPlayerClick}
            />
          ))}
        </div>
      )}
    </div>
  );
}
