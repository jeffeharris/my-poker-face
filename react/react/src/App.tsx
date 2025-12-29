import { useState, useEffect } from 'react'
import { PokerTable } from './components/game/PokerTable'
import { MobilePokerTable } from './components/mobile'
import { GameSelector } from './components/menus/GameSelector'
import { PlayerNameEntry } from './components/menus/PlayerNameEntry'
import { PersonalityManagerHTML } from './components/admin/PersonalityManagerHTML'
import { GameMenu } from './components/menus/GameMenu'
import { ThemedGameSelector } from './components/menus/ThemedGameSelector'
import { CustomGameConfig } from './components/menus/CustomGameConfig'
import { ElasticityDemo } from './components/debug/ElasticityDemo'
import { LoginForm } from './components/auth/LoginForm'
import { useAuth } from './hooks/useAuth'
import { useViewport } from './hooks/useViewport'
import { config } from './config'
import './App.css'

type ViewType = 'login' | 'name-entry' | 'game-menu' | 'selector' | 'table' | 'personalities' | 'themed-game' | 'custom-game' | 'elasticity-demo'

interface Theme {
  id: string;
  name: string;
  description: string;
  icon: string;
  personalities?: string[];
}

function App() {
  const { user, isLoading: authLoading, isAuthenticated, login, logout } = useAuth();
  const { isMobile } = useViewport();

  // Check localStorage for saved state on initial load
  const savedState = localStorage.getItem('pokerGameState');
  const parsedState = savedState ? JSON.parse(savedState) : null;
  
  // If we have a saved table view, validate it's not stale
  const initialView = parsedState?.currentView === 'table' ? 'login' : (parsedState?.currentView || 'login');
  
  const [currentView, setCurrentView] = useState<ViewType>(initialView)
  const [gameId, setGameId] = useState<string | null>(null) // Don't restore gameId to avoid loading non-existent games
  const [playerName, setPlayerName] = useState<string>(parsedState?.playerName || '')
  const [savedGamesCount, setSavedGamesCount] = useState(0)

  // Save state to localStorage whenever it changes
  useEffect(() => {
    const stateToSave = {
      currentView,
      gameId,
      playerName,
      timestamp: Date.now()
    };
    localStorage.setItem('pokerGameState', JSON.stringify(stateToSave));
  }, [currentView, gameId, playerName]);

  // Update view based on auth state
  useEffect(() => {
    if (!authLoading && isAuthenticated && currentView === 'login') {
      setPlayerName(user?.name || '');
      setCurrentView('game-menu');
    }
  }, [authLoading, isAuthenticated, user, currentView]);

  useEffect(() => {
    fetchSavedGamesCount();
  }, []);

  // Update page title based on current view
  useEffect(() => {
    const titles: Record<ViewType, string> = {
      'login': 'Login - My Poker Face',
      'name-entry': 'Choose Your Name - My Poker Face',
      'game-menu': 'Game Menu - My Poker Face',
      'selector': 'Select Game - My Poker Face',
      'table': gameId ? 'Playing - My Poker Face' : 'New Game - My Poker Face',
      'personalities': 'Manage Personalities - My Poker Face',
      'themed-game': 'Themed Game - My Poker Face',
      'custom-game': 'Custom Game - My Poker Face',
      'elasticity-demo': 'Elasticity Demo - My Poker Face'
    };
    
    document.title = titles[currentView] || 'My Poker Face';
  }, [currentView, gameId]);

  const fetchSavedGamesCount = async () => {
    try {
      const response = await fetch(`${config.API_URL}/games`, {
        credentials: 'include'
      });
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

  const handleLogin = async (name: string, isGuest: boolean) => {
    const result = await login(name, isGuest);
    if (result.success) {
      setPlayerName(name);
      setCurrentView('game-menu');
    }
  };

  const handleQuickPlay = async () => {
    try {
      const response = await fetch(`${config.API_URL}/api/new-game`, {
        method: 'POST',
        credentials: 'include',
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

  const handleStartCustomGame = async (selectedPersonalities: string[], llmConfig?: { model: string; reasoning_effort: string }) => {
    try {
      const response = await fetch(`${config.API_URL}/api/new-game`, {
        method: 'POST',
        credentials: 'include',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          playerName,
          personalities: selectedPersonalities,
          llm_config: llmConfig
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
        credentials: 'include',
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

      {/* Navigation - only show when in table view on desktop */}
      {currentView === 'table' && !isMobile && (
        <div style={{
          position: 'fixed',
          top: 10,
          left: 10,
          zIndex: 1000,
          display: 'flex',
          gap: '10px'
        }}>
          <button
            onClick={() => {
              // Clear the saved game when going back to menu
              setGameId(null);
              setCurrentView('game-menu');
            }}
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

      {/* User info - only show on game menu screen */}
      {isAuthenticated && user && currentView === 'game-menu' && (
        <div style={{
          position: 'fixed',
          top: 10,
          right: 10,
          zIndex: 1000,
          display: 'flex',
          alignItems: 'center',
          gap: '10px',
          padding: '8px 16px',
          backgroundColor: 'rgba(0, 0, 0, 0.7)',
          borderRadius: '20px',
          color: '#fff',
          fontSize: '14px'
        }}>
          <span>{user.name} {user.is_guest && '(Guest)'}</span>
          <button
            onClick={async () => {
              await logout();
              setCurrentView('login');
              setGameId(null);
            }}
            style={{
              padding: '4px 12px',
              backgroundColor: '#dc3545',
              color: '#fff',
              border: 'none',
              borderRadius: '4px',
              cursor: 'pointer',
              fontSize: '12px'
            }}
          >
            Logout
          </button>
        </div>
      )}

      {/* Views */}
      {currentView === 'login' && (
        <LoginForm onLogin={handleLogin} />
      )}
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
      {currentView === 'table' && (
        isMobile ? (
          <MobilePokerTable
            gameId={gameId}
            playerName={playerName}
            onGameCreated={(newGameId) => setGameId(newGameId)}
            onBack={() => {
              setGameId(null);
              setCurrentView('game-menu');
            }}
          />
        ) : (
          <PokerTable
            gameId={gameId}
            playerName={playerName}
            onGameCreated={(newGameId) => setGameId(newGameId)}
          />
        )
      )}
      {currentView === 'personalities' && (
        <PersonalityManagerHTML onBack={() => setCurrentView('selector')} />
      )}
      {currentView === 'elasticity-demo' && <ElasticityDemo />}
    </>
  )
}

export default App
