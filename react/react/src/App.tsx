import { useState, useEffect } from 'react'
import { Routes, Route, Navigate, useNavigate, useLocation } from 'react-router-dom'
import { type LucideIcon } from 'lucide-react'
import { GameSelector } from './components/menus/GameSelector'
import { PlayerNameEntry } from './components/menus/PlayerNameEntry'
import { PersonalityManager } from './components/admin/PersonalityManager'
import { GameMenu, type QuickPlayConfig } from './components/menus/GameMenu'
import { ThemedGameSelector } from './components/menus/ThemedGameSelector'
import { CustomGameConfig } from './components/menus/CustomGameConfig'
import { ElasticityDemo } from './components/debug/ElasticityDemo'
import { PromptDebugger } from './components/debug/PromptDebugger'
import { PromptPlayground } from './components/debug/PromptPlayground'
import { LoginForm } from './components/auth/LoginForm'
import { ProtectedRoute } from './components/auth/ProtectedRoute'
import { CareerStats } from './components/stats/CareerStats'
import { InstallPrompt } from './components/pwa/InstallPrompt'
import { BackButton, UserBadge } from './components/shared'
import { GamePage } from './components/game/GamePage'
import { AdminRoutes } from './components/admin/AdminRoutes'
import { useAuth } from './hooks/useAuth'
import { useViewport } from './hooks/useViewport'
import { config } from './config'
import './App.css'

// Game limit constants
const MAX_GAMES_GUEST = 3;
const MAX_GAMES_USER = 10;

interface Theme {
  id: string;
  name: string;
  description: string;
  icon: LucideIcon;
  personalities?: string[];
}

// Route titles for document.title
const ROUTE_TITLES: Record<string, string> = {
  '/login': 'Login - My Poker Face',
  '/name-entry': 'Choose Your Name - My Poker Face',
  '/menu': 'Game Menu - My Poker Face',
  '/games': 'Select Game - My Poker Face',
  '/game': 'Playing - My Poker Face',
  '/game/new/custom': 'Custom Game - My Poker Face',
  '/game/new/themed': 'Themed Game - My Poker Face',
  '/personalities': 'Manage Personalities - My Poker Face',
  '/stats': 'My Stats - My Poker Face',
  '/admin': 'Admin Dashboard - My Poker Face',
  '/elasticity-demo': 'Elasticity Demo - My Poker Face',
  '/prompt-debugger': 'Prompt Debugger - My Poker Face',
  '/prompt-playground': 'Prompt Playground - My Poker Face'
};

function App() {
  const { user, isLoading: authLoading, isAuthenticated, login, logout } = useAuth();
  const { isMobile } = useViewport();
  const navigate = useNavigate();
  const location = useLocation();

  const [playerName, setPlayerName] = useState<string>(user?.name || '')
  const [savedGamesCount, setSavedGamesCount] = useState(0)
  const [maxGamesError, setMaxGamesError] = useState<{ message: string; maxGames: number } | null>(null)

  // Hide splash screen once auth check is complete
  useEffect(() => {
    if (!authLoading) {
      const splash = document.getElementById('splash');
      if (splash) {
        splash.classList.add('hidden');
        // Remove from DOM after fade animation
        setTimeout(() => splash.remove(), 300);
      }
    }
  }, [authLoading]);

  // Update player name when user changes
  useEffect(() => {
    if (user?.name) {
      setPlayerName(user.name);
    }
  }, [user?.name]);

  // Update page title based on current route
  useEffect(() => {
    const basePath = location.pathname.split('/').slice(0, 3).join('/');
    // Check for game/:id pattern
    if (location.pathname.startsWith('/game/') && !location.pathname.includes('/new/')) {
      document.title = 'Playing - My Poker Face';
    } else if (location.pathname.startsWith('/admin')) {
      document.title = 'Admin Dashboard - My Poker Face';
    } else {
      document.title = ROUTE_TITLES[location.pathname] || ROUTE_TITLES[basePath] || 'My Poker Face';
    }
  }, [location.pathname]);

  // Fetch saved games count when authenticated
  useEffect(() => {
    if (isAuthenticated) {
      fetchSavedGamesCount();
    }
  }, [isAuthenticated]);

  // Redirect to menu after login if on login page
  useEffect(() => {
    if (!authLoading && isAuthenticated && location.pathname === '/login') {
      // Check if there was a stored location to return to
      const state = location.state as { from?: Location } | null;
      if (state?.from?.pathname && state.from.pathname !== '/login') {
        navigate(state.from.pathname, { replace: true });
      } else {
        navigate('/menu', { replace: true });
      }
    }
  }, [authLoading, isAuthenticated, location.pathname, location.state, navigate]);

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
    navigate(`/game/${selectedGameId}`);
  };

  const handleNameSubmit = (name: string) => {
    setPlayerName(name);
    navigate('/menu');
  };

  const handleLogin = async (name: string, isGuest: boolean) => {
    const result = await login(name, isGuest);
    if (result.success) {
      setPlayerName(name);
      navigate('/menu');
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
        navigate(`/game/${data.game_id}`);
      } else {
        checkMaxGamesError(response, data);
      }
    } catch (error) {
      console.error('Failed to create game:', error);
    }
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
        navigate(`/game/${data.game_id}`);
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

    navigate(`/game/${data.game_id}`);
  };

  const handleGamesChanged = () => {
    fetchSavedGamesCount();
  };

  const handleLogout = async () => {
    await logout();
    navigate('/login');
  };

  // Show loading state while checking auth
  if (authLoading) {
    return (
      <div className="loading-screen">
        <div className="loading-spinner" />
      </div>
    );
  }

  return (
    <>
      {/* Navigation - only show when in game view on desktop */}
      {location.pathname.startsWith('/game/') && !location.pathname.includes('/new/') && !isMobile && (
        <div className="app-nav app-nav--left">
          <BackButton
            onClick={() => navigate('/menu')}
            label="Back to Menu"
            position="relative"
          />
        </div>
      )}

      {/* User info - only show on game menu screen */}
      {isAuthenticated && user && location.pathname === '/menu' && (
        <UserBadge
          name={user.name}
          isGuest={user.is_guest}
          onLogout={handleLogout}
          className="user-badge--fixed"
        />
      )}

      {/* Routes */}
      <Routes>
        {/* Public routes */}
        <Route path="/login" element={
          isAuthenticated ? <Navigate to="/menu" replace /> : <LoginForm onLogin={handleLogin} />
        } />
        <Route path="/name-entry" element={
          <PlayerNameEntry onSubmit={handleNameSubmit} />
        } />

        {/* Protected routes */}
        <Route path="/menu" element={
          <ProtectedRoute>
            <GameMenu
              playerName={playerName}
              onQuickPlay={handleQuickPlay}
              onCustomGame={() => navigate('/game/new/custom')}
              onThemedGame={() => navigate('/game/new/themed')}
              onContinueGame={() => navigate('/games')}
              onViewStats={() => navigate('/stats')}
              onPromptDebugger={() => navigate('/prompt-debugger')}
              onPromptPlayground={() => navigate('/prompt-playground')}
              onAdminDashboard={() => navigate('/admin')}
              savedGamesCount={savedGamesCount}
            />
          </ProtectedRoute>
        } />

        <Route path="/games" element={
          <ProtectedRoute>
            <GameSelector
              onSelectGame={handleSelectGame}
              onBack={() => navigate('/menu')}
              onGamesChanged={handleGamesChanged}
            />
          </ProtectedRoute>
        } />

        <Route path="/game/new/custom" element={
          <ProtectedRoute>
            <CustomGameConfig
              onStartGame={handleStartCustomGame}
              onBack={() => navigate('/menu')}
            />
          </ProtectedRoute>
        } />

        <Route path="/game/new/themed" element={
          <ProtectedRoute>
            <ThemedGameSelector
              onSelectTheme={handleSelectTheme}
              onBack={() => navigate('/menu')}
            />
          </ProtectedRoute>
        } />

        <Route path="/game/:gameId" element={
          <ProtectedRoute>
            <GamePage playerName={playerName} />
          </ProtectedRoute>
        } />

        <Route path="/stats" element={
          <ProtectedRoute>
            <CareerStats onBack={() => navigate('/menu')} />
          </ProtectedRoute>
        } />

        <Route path="/admin/*" element={
          <ProtectedRoute>
            <AdminRoutes />
          </ProtectedRoute>
        } />

        <Route path="/personalities" element={
          <ProtectedRoute>
            <PersonalityManager onBack={() => navigate('/menu')} />
          </ProtectedRoute>
        } />

        {/* Debug routes */}
        <Route path="/elasticity-demo" element={<ElasticityDemo />} />
        <Route path="/prompt-debugger" element={
          <ProtectedRoute>
            <PromptDebugger onBack={() => navigate('/menu')} />
          </ProtectedRoute>
        } />
        <Route path="/prompt-playground" element={
          <ProtectedRoute>
            <PromptPlayground onBack={() => navigate('/menu')} />
          </ProtectedRoute>
        } />

        {/* Default redirect */}
        <Route path="/" element={<Navigate to={isAuthenticated ? '/menu' : '/login'} replace />} />
        <Route path="*" element={<Navigate to={isAuthenticated ? '/menu' : '/login'} replace />} />
      </Routes>

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
                  navigate('/games');
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
