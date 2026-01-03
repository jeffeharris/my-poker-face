import { useState, useEffect } from 'react';
import { config } from '../../config';
import { PageLayout, PageHeader } from '../shared';
import './GameSelector.css';

interface SavedGame {
  game_id: string;
  created_at: string;
  updated_at: string;
  phase: string;
  num_players: number;
  pot_size: number;
  player_names?: string[];
  active_players?: number;
  total_players?: number;
  human_stack?: number;
  big_blind?: number;
}

interface GameSelectorProps {
  onSelectGame: (gameId: string) => void;
  onBack: () => void;
}

export function GameSelector({ onSelectGame, onBack }: GameSelectorProps) {
  const [savedGames, setSavedGames] = useState<SavedGame[]>([]);
  const [loading, setLoading] = useState(true);
  const [deletingGameId, setDeletingGameId] = useState<string | null>(null);

  const fetchGames = () => {
    console.log('GameSelector: Fetching saved games...');
    fetch(`${config.API_URL}/api/games`, { credentials: 'include' })
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
      const response = await fetch(`${config.API_URL}/api/game/${gameId}`, {
        method: 'DELETE',
        credentials: 'include'
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

  if (loading) {
    return (
      <PageLayout variant="centered" glowColor="amethyst" maxWidth="lg">
        <h2 className="game-selector__loading">Loading saved games...</h2>
      </PageLayout>
    );
  }

  return (
    <PageLayout variant="top" glowColor="amethyst" maxWidth="lg">
      <PageHeader
        title="Saved Games"
        subtitle="Continue where you left off"
        onBack={onBack}
        titleVariant="primary"
      />

      <div className="game-selector__content">
        {savedGames.length > 0 && (
          <div className="saved-games">
            <div className="games-list">
              {savedGames.slice(0, 10).map(game => {
                const activePlayers = game.active_players ?? game.num_players;
                const totalPlayers = game.total_players ?? game.num_players;
                const playersText = activePlayers === totalPlayers
                  ? `${totalPlayers} players`
                  : `${activePlayers}/${totalPlayers} remaining`;

                return (
                <div
                  key={game.game_id}
                  className="saved-game-card"
                  style={{
                    display: 'block',
                    width: '100%',
                    padding: '16px 20px',
                    background: 'rgba(255, 255, 255, 0.08)',
                    border: '1px solid rgba(255, 255, 255, 0.15)',
                    borderRadius: '12px',
                    color: 'white',
                    textAlign: 'left',
                    marginBottom: '12px',
                    position: 'relative',
                    transition: 'all 0.2s'
                  }}
                  onMouseEnter={(e) => {
                    e.currentTarget.style.background = 'rgba(255, 255, 255, 0.12)';
                    e.currentTarget.style.borderColor = 'rgba(255, 255, 255, 0.25)';
                  }}
                  onMouseLeave={(e) => {
                    e.currentTarget.style.background = 'rgba(255, 255, 255, 0.08)';
                    e.currentTarget.style.borderColor = 'rgba(255, 255, 255, 0.15)';
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
                    {/* Header: Player names + date */}
                    <div style={{
                      display: 'flex',
                      justifyContent: 'space-between',
                      alignItems: 'flex-start',
                      marginBottom: '12px'
                    }}>
                      {game.player_names && game.player_names.length > 0 && (
                        <div style={{ fontSize: '16px', color: '#f1f5f9', fontWeight: 600, flex: 1 }}>
                          {game.player_names.join(', ')}
                        </div>
                      )}
                      <span style={{ color: '#64748b', fontSize: '12px', whiteSpace: 'nowrap', marginLeft: '12px' }}>
                        {new Date(game.updated_at).toLocaleDateString()}
                      </span>
                    </div>

                    {/* Stats grid */}
                    <div style={{
                      display: 'grid',
                      gridTemplateColumns: 'repeat(4, 1fr)',
                      gap: '12px 16px',
                      marginBottom: '12px'
                    }}>
                      {game.human_stack !== undefined && game.human_stack !== null && (
                        <div>
                          <div style={{ fontSize: '11px', color: '#64748b', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: '2px' }}>Your Stack</div>
                          <div style={{ fontSize: '16px', color: '#4ade80', fontWeight: 600 }}>${game.human_stack.toLocaleString()}</div>
                        </div>
                      )}
                      <div>
                        <div style={{ fontSize: '11px', color: '#64748b', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: '2px' }}>Current Pot</div>
                        <div style={{ fontSize: '16px', color: '#fbbf24', fontWeight: 600 }}>${game.pot_size.toLocaleString()}</div>
                      </div>
                      {game.big_blind && (
                        <div>
                          <div style={{ fontSize: '11px', color: '#64748b', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: '2px' }}>Blinds</div>
                          <div style={{ fontSize: '16px', color: '#a78bfa', fontWeight: 600 }}>{game.big_blind / 2}/{game.big_blind}</div>
                        </div>
                      )}
                      <div>
                        <div style={{ fontSize: '11px', color: '#64748b', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: '2px' }}>Players</div>
                        <div style={{ fontSize: '16px', color: '#94a3b8', fontWeight: 600 }}>{playersText}</div>
                      </div>
                    </div>

                    {/* Phase indicator */}
                    <div style={{
                      display: 'inline-block',
                      background: 'rgba(96, 165, 250, 0.15)',
                      color: '#60a5fa',
                      padding: '4px 10px',
                      borderRadius: '4px',
                      fontSize: '12px',
                      fontWeight: 500
                    }}>
                      {game.phase}
                    </div>
                  </button>
                  <button
                    className="delete-button"
                    onClick={(e) => handleDeleteGame(game.game_id, e)}
                    disabled={deletingGameId === game.game_id}
                    style={{
                      position: 'absolute',
                      bottom: '12px',
                      right: '12px',
                      background: 'rgba(127, 29, 29, 0.6)',
                      color: 'rgba(255, 255, 255, 0.7)',
                      border: '1px solid rgba(239, 68, 68, 0.3)',
                      borderRadius: '6px',
                      padding: '4px 8px',
                      cursor: deletingGameId === game.game_id ? 'not-allowed' : 'pointer',
                      fontSize: '11px',
                      fontWeight: 500,
                      transition: 'all 0.2s'
                    }}
                    onMouseEnter={(e) => {
                      if (deletingGameId !== game.game_id) {
                        e.currentTarget.style.background = '#dc2626';
                        e.currentTarget.style.color = 'white';
                        e.currentTarget.style.borderColor = '#dc2626';
                      }
                    }}
                    onMouseLeave={(e) => {
                      e.currentTarget.style.background = 'rgba(127, 29, 29, 0.6)';
                      e.currentTarget.style.color = 'rgba(255, 255, 255, 0.7)';
                      e.currentTarget.style.borderColor = 'rgba(239, 68, 68, 0.3)';
                    }}
                  >
                    {deletingGameId === game.game_id ? '...' : 'Delete'}
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
    </PageLayout>
  );
}