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
import { InstallPrompt } from './components/pwa/InstallPrompt'
import { BackButton, UserBadge } from './components/shared'
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

  // Check for active game that should be restored (from browser sleep/wake)
  const activeGameId = localStorage.getItem('activePokerGameId');
  // Check if user was logged in (auth will verify this, but we use it for initial view decision)
  const storedUser = localStorage.getItem('currentUser');

  // Determine initial view:
  // 1. If there's an active game AND a stored user, go straight to table
  // 2. If there's an active game but no user, go to login (auth effect will restore game after login)
  // 3. Otherwise use saved view (but not 'table' without an active game)
  const getInitialView = (): ViewType => {
    if (activeGameId && storedUser) {
      console.log('[App] Restoring to table with active game:', activeGameId);
      return 'table';
    }
    if (activeGameId && !storedUser) {
      return 'login';
    }
    if (parsedState?.currentView === 'table') {
      return 'login';
    }
    return parsedState?.currentView || 'login';
  };

  const [currentView, setCurrentView] = useState<ViewType>(getInitialView())
  const [gameId, setGameId] = useState<string | null>(activeGameId && storedUser ? activeGameId : null)
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
      // Check if there's an active game to restore after login
      const activeGame = localStorage.getItem('activePokerGameId');
      if (activeGame) {
        console.log('[App] Restoring active game after login:', activeGame);
        setGameId(activeGame);
        setCurrentView('table');
      } else {
        setCurrentView('game-menu');
      }
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

    if (response.status === 429) {
      throw new Error('Rate limit exceeded. Please wait a few minutes before starting a new game.');
    }

    if (!response.ok) {
      throw new Error('Failed to create game. Please try again.');
    }

    const data = await response.json();
    setGameId(data.game_id);
    setCurrentView('table');
  };

  return (
    <>

      {/* Navigation - only show when in table view on desktop */}
      {currentView === 'table' && !isMobile && (
        <div className="app-nav app-nav--left">
          <BackButton
            onClick={() => {
              setGameId(null);
              localStorage.removeItem('activePokerGameId');
              setCurrentView('game-menu');
            }}
            label="Back to Menu"
            position="relative"
          />
        </div>
      )}

      {/* User info - only show on game menu screen */}
      {isAuthenticated && user && currentView === 'game-menu' && (
        <UserBadge
          name={user.name}
          isGuest={user.is_guest}
          onLogout={async () => {
            await logout();
            localStorage.removeItem('activePokerGameId');
            setCurrentView('login');
            setGameId(null);
          }}
          className="user-badge--fixed"
        />
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
          onManagePersonalities={() => setCurrentView('personalities')}
          savedGamesCount={savedGamesCount}
        />
      )}
      {currentView === 'selector' && (
        <GameSelector
          onSelectGame={handleSelectGame}
          onBack={() => setCurrentView('game-menu')}
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
              localStorage.removeItem('activePokerGameId');
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
        <PersonalityManagerHTML onBack={() => setCurrentView('game-menu')} />
      )}
      {currentView === 'elasticity-demo' && <ElasticityDemo />}

      {/* PWA Install Prompt */}
      <InstallPrompt />
    </>
  )
}

export default App
