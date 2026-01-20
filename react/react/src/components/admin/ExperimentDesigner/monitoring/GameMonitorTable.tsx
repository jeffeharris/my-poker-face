/**
 * GameMonitorTable - Data table view for monitoring multiple games
 * Shows all games in a traditional table format with rows and columns
 */

import { Hash } from 'lucide-react';
import type { GameSnapshot } from './types';

interface GameMonitorTableProps {
  games: GameSnapshot[];
  onPlayerClick: (gameId: string, playerName: string) => void;
}

export function GameMonitorTable({ games, onPlayerClick }: GameMonitorTableProps) {
  const formatStack = (stack: number) => {
    if (stack >= 10000) {
      return `${(stack / 1000).toFixed(1)}k`;
    }
    return stack.toLocaleString();
  };

  return (
    <div className="game-monitor-table">
      <table className="game-monitor-table__table">
        <thead>
          <tr>
            <th>Variant</th>
            <th>Hand</th>
            <th>Phase</th>
            <th>Pot</th>
            <th>Players</th>
          </tr>
        </thead>
        <tbody>
          {games.map((game) => (
            <tr key={game.game_id} className="game-monitor-table__row">
              <td>
                <span className="game-monitor-table__variant">
                  {game.variant || 'Default'}
                </span>
              </td>
              <td className="game-monitor-table__hand">
                {game.hand_number}
              </td>
              <td>
                <span className="game-monitor-table__phase">
                  {game.phase.replace(/_/g, ' ')}
                </span>
              </td>
              <td className="game-monitor-table__pot">
                {formatStack(game.pot)}
              </td>
              <td>
                <div className="game-monitor-table__players">
                  {game.players.map((player) => (
                    <button
                      key={player.name}
                      className={`game-monitor-table__player ${
                        player.is_folded ? 'game-monitor-table__player--folded' : ''
                      } ${player.is_current ? 'game-monitor-table__player--current' : ''} ${
                        player.is_all_in ? 'game-monitor-table__player--all-in' : ''
                      }`}
                      onClick={() => onPlayerClick(game.game_id, player.name)}
                      type="button"
                      title={`${player.name}: ${formatStack(player.stack)} chips`}
                    >
                      <span className="game-monitor-table__player-name">
                        {player.name}
                      </span>
                      <span className="game-monitor-table__player-stack">
                        {formatStack(player.stack)}
                      </span>
                    </button>
                  ))}
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
