import { useState, useEffect } from 'react'
import { PokerTable } from './components/game/PokerTable'
import { GameSelector } from './components/GameSelector'
import { PlayerNameEntry } from './components/menus/PlayerNameEntry'
import { PersonalityManagerHTML } from './components/PersonalityManagerHTML'
import { GameMenu } from './components/GameMenu'
import { ThemedGameSelector } from './components/ThemedGameSelector'
import { CustomGameConfig } from './components/CustomGameConfig'
import { ElasticityDemo } from './components/ElasticityDemo'
import { config } from './config'
import './App.css'

type ViewType = 'name-entry' | 'game-menu' | 'selector' | 'table' | 'personalities' | 'themed-game' | 'custom-game' | 'elasticity-demo'

interface Theme {
  id: string;
  name: string;
  description: string;
  icon: string;
  personalities?: string[];
}

function App() {
  const [currentView, setCurrentView] = useState<ViewType>('name-entry')
  const [gameId, setGameId] = useState<string | null>(null)
  const [playerName, setPlayerName] = useState<string>('')
  const [savedGamesCount, setSavedGamesCount] = useState(0)

  useEffect(() => {
    fetchSavedGamesCount();
  }, []);

  const fetchSavedGamesCount = async () => {
    try {
      const response = await fetch(`${config.API_URL}/games`);
      const data = await response.json();
      setSavedGamesCount(data.games?.length || 0);
    } catch (error) {
      console.error('Failed to fetch saved games:', error);
    }
  };

  const handleSelectGame = (selectedGameId: string) => {
    setGameId(selectedGameId);
    setCurrentView('table');
  };

  const handleNewGame = () => {
    setGameId(null); // null means create new game
    setCurrentView('table');
  };

  const handleNameSubmit = (name: string) => {
    setPlayerName(name);
    setCurrentView('game-menu');
  };

  const handleQuickPlay = async () => {
    try {
      const response = await fetch(`${config.API_URL}/api/new-game`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ playerName }),
      });
      
      if (response.ok) {
        const data = await response.json();
        setGameId(data.game_id);
        setCurrentView('table');
      }
    } catch (error) {
      console.error('Failed to create game:', error);
    }
  };

  const handleCustomGame = () => {
    setCurrentView('custom-game');
  };

  const handleThemedGame = () => {
    setCurrentView('themed-game');
  };

  const handleContinueGame = () => {
    setCurrentView('selector');
  };

  const handleStartCustomGame = async (selectedPersonalities: string[]) => {
    try {
      const response = await fetch(`${config.API_URL}/api/new-game`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ 
          playerName,
          personalities: selectedPersonalities 
        }),
      });
      
      if (response.ok) {
        const data = await response.json();
        setGameId(data.game_id);
        setCurrentView('table');
      }
    } catch (error) {
      console.error('Failed to create custom game:', error);
    }
  };

  const handleSelectTheme = async (theme: Theme) => {
    if (!theme.personalities) return;
    
    try {
      const response = await fetch(`${config.API_URL}/api/new-game`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ 
          playerName,
          personalities: theme.personalities 
        }),
      });
      
      if (response.ok) {
        const data = await response.json();
        setGameId(data.game_id);
        setCurrentView('table');
      }
    } catch (error) {
      console.error('Failed to create themed game:', error);
    }
  };

  return (
    <>

      {/* Navigation - only show when in table view */}
      {currentView === 'table' && (
        <div style={{ 
          position: 'fixed', 
          top: 10, 
          left: 10, 
          zIndex: 1000,
          display: 'flex',
          gap: '10px'
        }}>
          <button 
            onClick={() => setCurrentView('game-menu')}
            style={{
              padding: '8px 16px',
              backgroundColor: '#666',
              color: '#fff',
              border: 'none',
              borderRadius: '4px',
              cursor: 'pointer'
            }}
          >
            ← Back to Menu
          </button>
        </div>
      )}

      {/* Views */}
      {currentView === 'name-entry' && (
        <PlayerNameEntry onSubmit={handleNameSubmit} />
      )}
      {currentView === 'game-menu' && (
        <GameMenu 
          playerName={playerName}
          onQuickPlay={handleQuickPlay}
          onCustomGame={handleCustomGame}
          onThemedGame={handleThemedGame}
          onContinueGame={handleContinueGame}
          savedGamesCount={savedGamesCount}
        />
      )}
      {currentView === 'selector' && (
        <GameSelector 
          onSelectGame={handleSelectGame} 
          onNewGame={handleNewGame}
          onManagePersonalities={() => setCurrentView('personalities')}
        />
      )}
      {currentView === 'custom-game' && (
        <CustomGameConfig 
          onStartGame={handleStartCustomGame}
          onBack={() => setCurrentView('game-menu')}
        />
      )}
      {currentView === 'themed-game' && (
        <ThemedGameSelector 
          onSelectTheme={handleSelectTheme}
          onBack={() => setCurrentView('game-menu')}
        />
      )}
      {currentView === 'table' && <PokerTable gameId={gameId} playerName={playerName} />}
      {currentView === 'personalities' && (
        <PersonalityManagerHTML onBack={() => setCurrentView('selector')} />
      )}
      {currentView === 'elasticity-demo' && <ElasticityDemo />}
    </>
  )
}

export default App
