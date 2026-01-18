import { useState, useEffect } from 'react'
import { type LucideIcon } from 'lucide-react'
import { GameSelector } from './components/menus/GameSelector'
import { PlayerNameEntry } from './components/menus/PlayerNameEntry'
import { PersonalityManager } from './components/admin/PersonalityManager'
import { AdminDashboard } from './components/admin/AdminDashboard'
import { GameMenu, type QuickPlayConfig } from './components/menus/GameMenu'
import { ThemedGameSelector } from './components/menus/ThemedGameSelector'
import { CustomGameConfig } from './components/menus/CustomGameConfig'
import { ElasticityDemo } from './components/debug/ElasticityDemo'
import { PromptDebugger } from './components/debug/PromptDebugger'
import { PromptPlayground } from './components/debug/PromptPlayground'
import { LoginForm } from './components/auth/LoginForm'
import { CareerStats } from './components/stats/CareerStats'
import { InstallPrompt } from './components/pwa/InstallPrompt'
import { BackButton, UserBadge, ResponsiveGameLayout } from './components/shared'
import { useAuth } from './hooks/useAuth'
import { useViewport } from './hooks/useViewport'
import { config } from './config'
import './App.css'

// Game limit constants
const MAX_GAMES_GUEST = 3;
const MAX_GAMES_USER = 10;

type ViewType = 'login' | 'name-entry' | 'game-menu' | 'selector' | 'table' | 'personalities' | 'themed-game' | 'custom-game' | 'elasticity-demo' | 'stats' | 'prompt-debugger' | 'prompt-playground' | 'admin-dashboard'

interface Theme {
  id: string;
  name: string;
  description: string;
  icon: LucideIcon;
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
  const [maxGamesError, setMaxGamesError] = useState<{ message: string; maxGames: number } | null>(null)

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

  // Fetch saved games count when authenticated and on game-menu view
  useEffect(() => {
    if (isAuthenticated && currentView === 'game-menu') {
      fetchSavedGamesCount();
    }
  }, [isAuthenticated, currentView]);

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
      'elasticity-demo': 'Elasticity Demo - My Poker Face',
      'stats': 'My Stats - My Poker Face',
      'prompt-debugger': 'Prompt Debugger - My Poker Face',
      'prompt-playground': 'Prompt Playground - My Poker Face',
      'admin-dashboard': 'Admin Dashboard - My Poker Face'
    };
    
    document.title = titles[currentView] || 'My Poker Face';
  }, [currentView, gameId]);

  const fetchSavedGamesCount = async () => {
    try {
      const response = await fetch(`${config.API_URL}/api/games`, {
        credentials: 'include'
      });
      const data = await response.json();
      setSavedGamesCount(data.games?.length || 0);
    } catch (error) {
      console.error('Failed to fetch saved games:', error);
    }
  };

  // Helper to check for and handle max games limit error
  const checkMaxGamesError = (response: Response, data: { error?: string }): boolean => {
    if (response.status === 400 && data.error?.includes('Game limit reached')) {
      const maxGames = user?.is_guest ? MAX_GAMES_GUEST : MAX_GAMES_USER;
      setMaxGamesError({ message: data.error, maxGames });
      return true;
    }
    return false;
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

  const handleQuickPlay = async (quickPlayConfig: QuickPlayConfig) => {
    try {
      // Calculate starting stack based on big blinds
      const bigBlind = 50;
      const startingStack = quickPlayConfig.startingBB * bigBlind;

      const response = await fetch(`${config.API_URL}/api/new-game`, {
        method: 'POST',
        credentials: 'include',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          playerName,
          starting_stack: startingStack,
          big_blind: bigBlind,
          opponent_count: quickPlayConfig.opponents,
        }),
      });

      const data = await response.json();

      if (response.ok) {
        setGameId(data.game_id);
        setCurrentView('table');
      } else {
        checkMaxGamesError(response, data);
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

  const handleGamesChanged = () => {
    fetchSavedGamesCount();
  };

  const handleStartCustomGame = async (
    selectedPersonalities: Array<string | { name: string; llm_config: { provider: string; model: string; reasoning_effort?: string } }>,
    llmConfig?: { provider: string; model: string; reasoning_effort: string; starting_stack?: number; big_blind?: number; blind_growth?: number; blinds_increase?: number; max_blind?: number }
  ) => {
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
          llm_config: llmConfig,
          starting_stack: llmConfig?.starting_stack,
          big_blind: llmConfig?.big_blind,
          blind_growth: llmConfig?.blind_growth,
          blinds_increase: llmConfig?.blinds_increase,
          max_blind: llmConfig?.max_blind
        }),
      });

      const data = await response.json();

      if (response.ok) {
        setGameId(data.game_id);
        setCurrentView('table');
      } else {
        checkMaxGamesError(response, data);
      }
    } catch (error) {
      console.error('Failed to create custom game:', error);
    }
  };

  const handleSelectTheme = async (theme: Theme) => {
    if (!theme.personalities) return;

    let response: Response;
    try {
      response = await fetch(`${config.API_URL}/api/new-game`, {
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
    } catch {
      throw new Error('Network error. Please check your connection and try again.');
    }

    if (response.status === 429) {
      throw new Error('Rate limit exceeded. Please wait a few minutes before starting a new game.');
    }

    const data = await response.json();

    if (checkMaxGamesError(response, data)) {
      return;
    }

    if (!response.ok) {
      throw new Error('Failed to create game. Please try again.');
    }

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
          onViewStats={() => setCurrentView('stats')}
          onPromptDebugger={() => setCurrentView('prompt-debugger')}
          onPromptPlayground={() => setCurrentView('prompt-playground')}
          onAdminDashboard={() => setCurrentView('admin-dashboard')}
          savedGamesCount={savedGamesCount}
        />
      )}
      {currentView === 'selector' && (
        <GameSelector
          onSelectGame={handleSelectGame}
          onBack={() => setCurrentView('game-menu')}
          onGamesChanged={handleGamesChanged}
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
        <ResponsiveGameLayout
          gameId={gameId}
          playerName={playerName}
          onGameCreated={(newGameId) => setGameId(newGameId)}
          onBack={() => {
            setGameId(null);
            localStorage.removeItem('activePokerGameId');
            setCurrentView('game-menu');
          }}
        />
      )}
      {currentView === 'personalities' && (
        <PersonalityManager onBack={() => setCurrentView('game-menu')} />
      )}
      {currentView === 'elasticity-demo' && <ElasticityDemo />}
      {currentView === 'stats' && (
        <CareerStats onBack={() => setCurrentView('game-menu')} />
      )}
      {currentView === 'prompt-debugger' && (
        <PromptDebugger onBack={() => setCurrentView('game-menu')} />
      )}
      {currentView === 'prompt-playground' && (
        <PromptPlayground onBack={() => setCurrentView('game-menu')} />
      )}
      {currentView === 'admin-dashboard' && (
        <AdminDashboard onBack={() => setCurrentView('game-menu')} />
      )}

      {/* Max Games Error Modal */}
      {maxGamesError && (
        <div className="max-games-modal">
          <div className="max-games-modal__content">
            <div className="max-games-modal__icon">
              <span role="img" aria-label="warning">&#x26A0;&#xFE0F;</span>
            </div>
            <h2 className="max-games-modal__title">
              Game Limit Reached
            </h2>
            <p className="max-games-modal__message">
              You have reached the maximum of {maxGamesError.maxGames} saved game{maxGamesError.maxGames > 1 ? 's' : ''}.
              Would you like to manage your saved games to make room for a new one?
            </p>
            <div className="max-games-modal__actions">
              <button
                className="max-games-modal__btn max-games-modal__btn--primary"
                onClick={() => {
                  setMaxGamesError(null);
                  setCurrentView('selector');
                }}
              >
                Manage Games
              </button>
              <button
                className="max-games-modal__btn max-games-modal__btn--secondary"
                onClick={() => setMaxGamesError(null)}
              >
                Cancel
              </button>
            </div>
          </div>
        </div>
      )}

      {/* PWA Install Prompt */}
      <InstallPrompt />
    </>
  )
}

export default App
