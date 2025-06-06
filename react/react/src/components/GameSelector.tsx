import { useState, useEffect } from 'react';
import { config } from '../config';
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
  const [deletingGameId, setDeletingGameId] = useState<string | null>(null);

  const fetchGames = () => {
    console.log('GameSelector: Fetching saved games...');
    fetch(`${config.API_URL}/games`)
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
  };

  useEffect(() => {
    fetchGames();
  }, []);

  const handleDeleteGame = async (gameId: string, event: React.MouseEvent) => {
    event.stopPropagation(); // Prevent triggering the game load
    
    if (!confirm(`Are you sure you want to delete this game?`)) {
      return;
    }

    setDeletingGameId(gameId);
    
    try {
      const response = await fetch(`${config.API_URL}/game/${gameId}`, {
        method: 'DELETE'
      });
      
      if (response.ok) {
        // Remove from local state immediately for better UX
        setSavedGames(prev => prev.filter(game => game.game_id !== gameId));
        // Optionally refetch to ensure sync
        fetchGames();
      } else {
        const error = await response.json();
        alert(`Failed to delete game: ${error.error || 'Unknown error'}`);
      }
    } catch (err) {
      console.error('Error deleting game:', err);
      alert('Failed to delete game');
    } finally {
      setDeletingGameId(null);
    }
  };

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
                <div
                  key={game.game_id}
                  className="saved-game-card"
                  style={{
                    display: 'block',
                    width: '100%',
                    padding: '20px',
                    background: 'rgba(255, 255, 255, 0.1)',
                    border: '2px solid rgba(255, 255, 255, 0.3)',
                    borderRadius: '12px',
                    color: 'white',
                    textAlign: 'left',
                    marginBottom: '10px',
                    position: 'relative',
                    transition: 'all 0.2s'
                  }}
                  onMouseEnter={(e) => {
                    const deleteBtn = e.currentTarget.querySelector('.delete-button') as HTMLElement;
                    if (deleteBtn) deleteBtn.style.opacity = '1';
                  }}
                  onMouseLeave={(e) => {
                    const deleteBtn = e.currentTarget.querySelector('.delete-button') as HTMLElement;
                    if (deleteBtn && deletingGameId !== game.game_id) {
                      deleteBtn.style.opacity = '0';
                    }
                  }}
                >
                  <button
                    onClick={() => onSelectGame(game.game_id)}
                    style={{
                      background: 'none',
                      border: 'none',
                      color: 'inherit',
                      cursor: 'pointer',
                      width: '100%',
                      textAlign: 'left',
                      padding: 0
                    }}
                  >
                    <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '8px', paddingRight: '60px' }}>
                      <span style={{ color: '#60a5fa', fontWeight: 'bold' }}>{getPhaseDisplay(game.phase)}</span>
                      <span style={{ color: '#4ade80', fontWeight: 'bold' }}>${game.pot_size} pot</span>
                    </div>
                    <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '14px', paddingRight: '60px' }}>
                      <span style={{ color: '#94a3b8' }}>{game.num_players} players</span>
                      <span style={{ color: '#94a3b8' }}>{new Date(game.updated_at).toLocaleDateString()}</span>
                    </div>
                  </button>
                  <button
                    className="delete-button"
                    onClick={(e) => handleDeleteGame(game.game_id, e)}
                    disabled={deletingGameId === game.game_id}
                    style={{
                      position: 'absolute',
                      bottom: '10px',
                      right: '10px',
                      background: '#ef4444',
                      color: 'white',
                      border: 'none',
                      borderRadius: '6px',
                      padding: '4px 10px',
                      cursor: deletingGameId === game.game_id ? 'not-allowed' : 'pointer',
                      fontSize: '11px',
                      fontWeight: 'bold',
                      opacity: 0,
                      transition: 'all 0.2s',
                      boxShadow: '0 2px 4px rgba(0,0,0,0.2)'
                    }}
                    onMouseEnter={(e) => {
                      if (deletingGameId !== game.game_id) {
                        e.currentTarget.style.background = '#dc2626';
                        e.currentTarget.style.transform = 'scale(1.05)';
                      }
                    }}
                    onMouseLeave={(e) => {
                      e.currentTarget.style.background = '#ef4444';
                      e.currentTarget.style.transform = 'scale(1)';
                    }}
                  >
                    {deletingGameId === game.game_id ? 'Deleting...' : 'Delete'}
                  </button>
                </div>
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