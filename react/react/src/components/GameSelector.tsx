import { useState, useEffect } from 'react';
import './GameSelector.css';

interface SavedGame {
  game_id: string;
  created_at: string;
  updated_at: string;
  phase: string;
  num_players: number;
  pot_size: number;
}

interface GameSelectorProps {
  onSelectGame: (gameId: string) => void;
  onNewGame: () => void;
}

export function GameSelector({ onSelectGame, onNewGame }: GameSelectorProps) {
  const [savedGames, setSavedGames] = useState<SavedGame[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    // Fetch saved games
    console.log('GameSelector: Fetching saved games...');
    fetch('http://localhost:5000/games')
      .then(res => res.json())
      .then(data => {
        console.log('GameSelector: Received games data:', data);
        setSavedGames(data.games || []);
        setLoading(false);
      })
      .catch(err => {
        console.error('Failed to fetch games:', err);
        setLoading(false);
      });
  }, []);

  const getPhaseDisplay = (phase: string) => {
    const phaseMap: { [key: string]: string } = {
      '0': 'Initializing',
      '1': 'Pre-Flop',
      '2': 'Flop',
      '3': 'Turn',
      '4': 'River',
      '5': 'Showdown',
      '6': 'Hand Over',
      '7': 'Game Over',
      '8': 'Dealing Cards'
    };
    return phaseMap[phase] || phase;
  };

  if (loading) {
    return (
      <div className="game-selector loading">
        <h2>Loading saved games...</h2>
      </div>
    );
  }

  return (
    <div className="game-selector">
      <div className="selector-header">
        <h1>🎰 Poker Game</h1>
        <p>Start a new game or continue a saved one</p>
        <p style={{ fontSize: '12px', color: '#666', marginTop: '10px' }}>
          Note: Game loading is experimental and may not work for all saved games
        </p>
      </div>

      <div className="game-options">
        <button className="new-game-button" onClick={onNewGame}>
          <div className="button-icon">🆕</div>
          <div className="button-text">
            <h3>New Game</h3>
            <p>Start fresh with AI opponents</p>
          </div>
        </button>

        {savedGames.length > 0 && (
          <div className="saved-games">
            <h2>Continue Playing</h2>
            <div className="games-list">
              {savedGames.slice(0, 10).map(game => {
                console.log('Rendering game:', game);
                return (
                <button
                  key={game.game_id}
                  className="saved-game-card"
                  onClick={() => onSelectGame(game.game_id)}
                  style={{
                    display: 'block',
                    width: '100%',
                    padding: '20px',
                    background: 'rgba(255, 255, 255, 0.1)',
                    border: '2px solid rgba(255, 255, 255, 0.3)',
                    borderRadius: '12px',
                    color: 'white',
                    textAlign: 'left',
                    cursor: 'pointer',
                    marginBottom: '10px'
                  }}
                >
                  <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '8px' }}>
                    <span style={{ color: '#60a5fa', fontWeight: 'bold' }}>{getPhaseDisplay(game.phase)}</span>
                    <span style={{ color: '#4ade80', fontWeight: 'bold' }}>${game.pot_size} pot</span>
                  </div>
                  <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '14px' }}>
                    <span style={{ color: '#94a3b8' }}>{game.num_players} players</span>
                    <span style={{ color: '#94a3b8' }}>{new Date(game.updated_at).toLocaleDateString()}</span>
                  </div>
                </button>
                );
              })}
            </div>
          </div>
        )}

        {savedGames.length === 0 && (
          <div className="no-saved-games">
            <p>No saved games found</p>
          </div>
        )}
      </div>
    </div>
  );
}