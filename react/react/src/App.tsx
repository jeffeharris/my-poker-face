import { useState, useEffect, useMemo, lazy, Suspense } from 'react';
import { Routes, Route, Navigate, useNavigate, useLocation } from 'react-router-dom';
import { ErrorBoundary } from './components/ErrorBoundary';
import { HomeMenu } from './components/menus/HomeMenu';
import { TournamentMenu, type QuickPlayConfig } from './components/menus/TournamentMenu';
import { LoginForm } from './components/auth/LoginForm';
import { ProtectedRoute } from './components/auth/ProtectedRoute';
import { GamePage } from './components/game/GamePage';
import { useAuth } from './hooks/useAuth';
import { useOnlineStatus } from './hooks/useOnlineStatus';
import { useUsageStats } from './hooks/useUsageStats';
import { useNicknameOverridesStore } from './stores/nicknameOverridesStore';
import { fetchNicknameOverrides } from './components/character/api';
import { ShuffleLoading, GuestLimitModal } from './components/shared';
import { pickQuote } from './components/game/WinnerAnnouncement/quote-flavor';
import { logger } from './utils/logger';
import { config } from './config';
import { type Theme } from './types/theme';
import toast, { Toaster } from 'react-hot-toast';
import './App.css';

// Lazy-loaded routes — only downloaded when navigated to
const GameSelector = lazy(() =>
  import('./components/menus/GameSelector').then((m) => ({ default: m.GameSelector }))
);
const PlayerNameEntry = lazy(() =>
  import('./components/menus/PlayerNameEntry').then((m) => ({ default: m.PlayerNameEntry }))
);
const PersonalityManager = lazy(() =>
  import('./components/admin/PersonalityManager').then((m) => ({ default: m.PersonalityManager }))
);
const ThemedGameSelector = lazy(() =>
  import('./components/menus/ThemedGameSelector').then((m) => ({ default: m.ThemedGameSelector }))
);
const CustomGameConfig = lazy(() =>
  import('./components/menus/CustomGameConfig').then((m) => ({ default: m.CustomGameConfig }))
);
const CareerStats = lazy(() =>
  import('./components/stats/CareerStats').then((m) => ({ default: m.CareerStats }))
);
const ProfilePage = lazy(() =>
  import('./components/profile/ProfilePage').then((m) => ({ default: m.ProfilePage }))
);
const InstallPrompt = lazy(() =>
  import('./components/pwa/InstallPrompt').then((m) => ({ default: m.InstallPrompt }))
);
const AdminRoutes = lazy(() =>
  import('./components/admin/AdminRoutes').then((m) => ({ default: m.AdminRoutes }))
);
const LandingPage = lazy(() =>
  import('./components/landing').then((m) => ({ default: m.LandingPage }))
);
const Lobby = lazy(() => import('./components/cash/Lobby').then((m) => ({ default: m.Lobby })));
const PrivacyPolicy = lazy(() =>
  import('./components/legal').then((m) => ({ default: m.PrivacyPolicy }))
);
const TermsOfService = lazy(() =>
  import('./components/legal').then((m) => ({ default: m.TermsOfService }))
);
const WinnerLayoutSandbox = lazy(() =>
  import('./components/dev/WinnerLayoutSandbox').then((m) => ({ default: m.WinnerLayoutSandbox }))
);

// Fallback game limit values when usageStats hasn't loaded yet
const MAX_GAMES_GUEST = 1;
const MAX_GAMES_USER = 10;

// Route titles for document.title
const ROUTE_TITLES: Record<string, string> = {
  '/login': 'Login - My Poker Face',
  '/name-entry': 'Choose Your Name - My Poker Face',
  '/menu': 'Game Menu - My Poker Face',
  '/menu/tournament': 'Tournaments - My Poker Face',
  '/games': 'Select Game - My Poker Face',
  '/game': 'Playing - My Poker Face',
  '/game/new/custom': 'Custom Game - My Poker Face',
  '/game/new/themed': 'Themed Game - My Poker Face',
  '/personalities': 'Manage Personalities - My Poker Face',
  '/cash': 'Career - My Poker Face',
  '/stats': 'My Stats - My Poker Face',
  '/profile': 'Your Profile - My Poker Face',
  '/admin': 'Admin Dashboard - My Poker Face',
  '/privacy': 'Privacy Policy - My Poker Face',
  '/terms': 'Terms of Service - My Poker Face',
  '/': 'My Poker Face - Play Poker Against AI',
};

function App() {
  const { user, isLoading: authLoading, isAuthenticated, login } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  useOnlineStatus();
  const { stats: usageStats } = useUsageStats();
  const [showGuestLimitModal, setShowGuestLimitModal] = useState(false);

  const [playerName, setPlayerName] = useState<string>(user?.name || '');
  const [savedGamesCount, setSavedGamesCount] = useState(0);
  const [maxGamesError, setMaxGamesError] = useState<{ message: string; maxGames: number } | null>(
    null
  );
  const [isCreatingGame, setIsCreatingGame] = useState(false);
  const [loadingSubmessage, setLoadingSubmessage] = useState(
    'Preparing the table and seating your opponents'
  );

  // Fresh quote each time game creation kicks off.
  const creatingGameQuote = useMemo(() => {
    if (!isCreatingGame) return undefined;
    const q = pickQuote('between_hands');
    return q ? { text: q.text, attribution: q.attribution } : undefined;
  }, [isCreatingGame]);

  // Check if guest hand limit is already reached on load
  useEffect(() => {
    if (usageStats?.hands_limit_reached) {
      setShowGuestLimitModal(true);
    }
  }, [usageStats?.hands_limit_reached]);

  // Update player name when user changes
  useEffect(() => {
    if (user?.name) {
      setPlayerName(user.name);
    }
  }, [user?.name]);

  // Hydrate the per-viewer nickname-override map on auth so opponent
  // labels everywhere (table, chat, heads-up panel, etc.) reflect
  // private aliases the player set in the dossier. Re-runs when
  // the user identity flips (login/logout) so the override set is
  // never stale to who's looking.
  //
  // Reset runs unconditionally at the top: this prevents a stale
  // prior-user map from leaking into a fresh identity's hydrate
  // (hydrate merges, with local edits winning, so without this
  // reset the prior user's edits would survive an identity swap).
  const hydrateOverrides = useNicknameOverridesStore((s) => s.hydrate);
  const resetOverrides = useNicknameOverridesStore((s) => s.reset);
  useEffect(() => {
    resetOverrides();
    if (!isAuthenticated || !user?.id) return;
    let cancelled = false;
    fetchNicknameOverrides()
      .then((map) => {
        if (!cancelled) hydrateOverrides(map);
      })
      .catch((e) => {
        // Soft-fail: leave the (empty) store alone so canonical
        // nicknames keep showing — the existing behaviour pre-feature.
        logger.warn('[nickname-overrides] fetch failed', e);
      });
    return () => {
      cancelled = true;
    };
  }, [isAuthenticated, user?.id, hydrateOverrides, resetOverrides]);

  // Update page title based on current route
  useEffect(() => {
    const basePath = location.pathname.split('/').slice(0, 3).join('/');
    // Check for game/:id pattern
    if (location.pathname.startsWith('/game/') && !location.pathname.includes('/new/')) {
      document.title = 'Playing - My Poker Face';
    } else if (location.pathname.match(/^\/admin\/experiments\/\d+$/)) {
      document.title = 'Experiment Details - My Poker Face';
    } else if (location.pathname.startsWith('/admin')) {
      document.title = 'Admin Dashboard - My Poker Face';
    } else {
      document.title = ROUTE_TITLES[location.pathname] || ROUTE_TITLES[basePath] || 'My Poker Face';
    }
  }, [location.pathname]);

  // Fetch saved games count when authenticated or navigating to a menu page
  useEffect(() => {
    if (
      isAuthenticated &&
      (location.pathname === '/menu' || location.pathname === '/menu/tournament')
    ) {
      fetchSavedGamesCount();
    }
  }, [isAuthenticated, location.pathname]);

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
        credentials: 'include',
      });
      const data = await response.json();
      setSavedGamesCount(data.games?.length || 0);
    } catch (error) {
      logger.error('Failed to fetch saved games:', error);
    }
  };

  // Helper to check for and handle max games limit error
  const checkMaxGamesError = (
    response: Response,
    data: { error?: string; code?: string }
  ): boolean => {
    if (
      (response.status === 400 || response.status === 403) &&
      (data.code === 'GUEST_LIMIT_GAMES' || data.error?.includes('Game limit reached'))
    ) {
      const maxGames =
        usageStats?.max_active_games ?? (user?.is_guest ? MAX_GAMES_GUEST : MAX_GAMES_USER);
      setMaxGamesError({ message: data.error || 'Game limit reached', maxGames });
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
    if (isCreatingGame) return;
    setIsCreatingGame(true);
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
          game_mode: quickPlayConfig.gameMode,
          blind_growth: quickPlayConfig.blindGrowth,
          blinds_increase: quickPlayConfig.blindsIncrease,
          max_blind: quickPlayConfig.maxBlind,
        }),
      });

      const data = await response.json();

      if (response.ok) {
        navigate(`/game/${data.game_id}`);
      } else {
        if (!checkMaxGamesError(response, data)) {
          toast.error(data.error || 'Failed to create game. Please try again.');
        }
      }
    } catch (error) {
      logger.error('Failed to create game:', error);
      toast.error('Failed to create game. Please try again.');
    } finally {
      setIsCreatingGame(false);
    }
  };

  const handleStartCustomGame = async (
    selectedPersonalities: Array<
      | string
      | {
          name: string;
          llm_config: { provider: string; model: string; reasoning_effort?: string };
          game_mode?: string;
        }
    >,
    llmConfig?: {
      provider: string;
      model: string;
      reasoning_effort: string;
      starting_stack?: number;
      big_blind?: number;
      blind_growth?: number;
      blinds_increase?: number;
      max_blind?: number;
      ai_chat?: boolean;
    },
    gameMode?: string,
    botTypes?: Record<
      string,
      'chaos' | 'standard' | 'lean' | 'sharp' | 'casebot' | 'gto_lite' | 'baseline_solver'
    >
  ) => {
    if (isCreatingGame) return;
    setIsCreatingGame(true);
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
          game_mode: gameMode,
          starting_stack: llmConfig?.starting_stack,
          big_blind: llmConfig?.big_blind,
          blind_growth: llmConfig?.blind_growth,
          blinds_increase: llmConfig?.blinds_increase,
          max_blind: llmConfig?.max_blind,
          ai_chat: llmConfig?.ai_chat ?? true,
          ...(botTypes && Object.keys(botTypes).length > 0 ? { bot_types: botTypes } : {}),
        }),
      });

      const data = await response.json();

      if (response.ok) {
        navigate(`/game/${data.game_id}`);
      } else {
        if (!checkMaxGamesError(response, data)) {
          toast.error(data.error || 'Failed to create game. Please try again.');
        }
      }
    } catch (error) {
      logger.error('Failed to create custom game:', error);
      toast.error('Failed to create game. Please try again.');
    } finally {
      setIsCreatingGame(false);
    }
  };

  const handleSelectTheme = async (theme: Theme) => {
    if (!theme.personalities) return;
    if (isCreatingGame) return;
    setIsCreatingGame(true);

    try {
      if (theme.themeDescription) {
        setLoadingSubmessage(theme.themeDescription);
      }
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
            personalities: theme.personalities,
            game_mode: theme.game_mode,
            starting_stack: theme.starting_stack,
            big_blind: theme.big_blind,
            blind_growth: theme.blind_growth,
            blinds_increase: theme.blinds_increase,
            max_blind: theme.max_blind,
          }),
        });
      } catch {
        throw new Error('Network error. Please check your connection and try again.');
      }

      if (response.status === 429) {
        throw new Error(
          'Rate limit exceeded. Please wait a few minutes before starting a new game.'
        );
      }

      const data = await response.json();

      if (checkMaxGamesError(response, data)) {
        return;
      }

      if (!response.ok) {
        throw new Error('Failed to create game. Please try again.');
      }

      navigate(`/game/${data.game_id}`);
    } finally {
      setIsCreatingGame(false);
      setLoadingSubmessage('Preparing the table and seating your opponents');
    }
  };

  const handleGamesChanged = () => {
    fetchSavedGamesCount();
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
      {/* Toast notifications */}
      <Toaster
        position="top-right"
        toastOptions={{
          duration: 4000,
        }}
      />

      {/* Routes */}
      <ErrorBoundary>
        <Suspense
          fallback={
            <div className="loading-screen">
              <div className="loading-spinner" />
            </div>
          }
        >
          <Routes>
            {/* Public routes */}
            <Route
              path="/login"
              element={
                isAuthenticated ? (
                  <Navigate to="/menu" replace />
                ) : (
                  <LoginForm onLogin={handleLogin} />
                )
              }
            />
            <Route path="/name-entry" element={<PlayerNameEntry onSubmit={handleNameSubmit} />} />
            <Route path="/privacy" element={<PrivacyPolicy />} />
            <Route path="/terms" element={<TermsOfService />} />
            <Route path="/dev/winner-layout" element={<WinnerLayoutSandbox />} />

            {/* Protected routes */}
            <Route
              path="/menu"
              element={
                <ProtectedRoute>
                  <HomeMenu
                    playerName={playerName}
                    onCashMode={() => navigate('/cash')}
                    onTournament={() => navigate('/menu/tournament')}
                    onAdminDashboard={() => navigate('/admin')}
                  />
                </ProtectedRoute>
              }
            />

            <Route
              path="/menu/tournament"
              element={
                <ProtectedRoute>
                  <TournamentMenu
                    playerName={playerName}
                    onQuickPlay={handleQuickPlay}
                    onCustomGame={() => navigate('/game/new/custom')}
                    onThemedGame={() => navigate('/game/new/themed')}
                    onContinueGame={() => navigate('/games')}
                    onViewStats={() => navigate('/stats')}
                    onAdminDashboard={() => navigate('/admin')}
                    onBack={() => navigate('/menu')}
                    savedGamesCount={savedGamesCount}
                    isCreatingGame={isCreatingGame}
                  />
                </ProtectedRoute>
              }
            />

            <Route
              path="/games"
              element={
                <ProtectedRoute>
                  <GameSelector
                    onSelectGame={handleSelectGame}
                    onBack={() => navigate('/menu/tournament')}
                    onGamesChanged={handleGamesChanged}
                  />
                </ProtectedRoute>
              }
            />

            <Route
              path="/game/new/custom"
              element={
                <ProtectedRoute>
                  <CustomGameConfig
                    onStartGame={handleStartCustomGame}
                    onBack={() => navigate('/menu/tournament')}
                    isCreatingGame={isCreatingGame}
                  />
                </ProtectedRoute>
              }
            />

            <Route
              path="/game/new/themed"
              element={
                <ProtectedRoute>
                  <ThemedGameSelector
                    onSelectTheme={handleSelectTheme}
                    onBack={() => navigate('/menu/tournament')}
                    isCreatingGame={isCreatingGame}
                  />
                </ProtectedRoute>
              }
            />

            <Route
              path="/game/:gameId"
              element={
                <ProtectedRoute>
                  <ErrorBoundary
                    fallbackAction={{ label: 'Return to Menu', onClick: () => navigate('/menu') }}
                  >
                    <GamePage playerName={playerName} />
                  </ErrorBoundary>
                </ProtectedRoute>
              }
            />

            <Route
              path="/stats"
              element={
                <ProtectedRoute>
                  <CareerStats onBack={() => navigate('/menu/tournament')} />
                </ProtectedRoute>
              }
            />

            <Route
              path="/profile"
              element={
                <ProtectedRoute>
                  <ProfilePage onBack={() => navigate('/menu')} />
                </ProtectedRoute>
              }
            />

            <Route
              path="/admin/*"
              element={
                <ProtectedRoute>
                  <AdminRoutes />
                </ProtectedRoute>
              }
            />

            <Route
              path="/cash"
              element={
                <ProtectedRoute>
                  <Lobby />
                </ProtectedRoute>
              }
            />

            <Route
              path="/personalities"
              element={
                <ProtectedRoute>
                  <PersonalityManager onBack={() => navigate('/menu')} />
                </ProtectedRoute>
              }
            />

            {/* Landing page and fallback */}
            <Route
              path="/"
              element={isAuthenticated ? <Navigate to="/menu" replace /> : <LandingPage />}
            />
            <Route path="*" element={<Navigate to={isAuthenticated ? '/menu' : '/'} replace />} />
          </Routes>
        </Suspense>
      </ErrorBoundary>

      {/* ShuffleLoading overlay - blocks all interaction during game creation */}
      <ShuffleLoading
        isVisible={isCreatingGame}
        message="Setting up your game"
        submessage={loadingSubmessage}
        exitStyle="slide"
        quote={creatingGameQuote}
      />

      {/* Max Games Error Modal */}
      {maxGamesError && (
        <div className="max-games-modal">
          <div className="max-games-modal__content">
            <div className="max-games-modal__icon">
              <span role="img" aria-label="warning">
                &#x26A0;&#xFE0F;
              </span>
            </div>
            <h2 className="max-games-modal__title">Game Limit Reached</h2>
            <p className="max-games-modal__message">
              You have reached the maximum of {maxGamesError.maxGames} saved game
              {maxGamesError.maxGames > 1 ? 's' : ''}. Would you like to manage your saved games to
              make room for a new one?
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

      {/* Guest Hand Limit Modal */}
      {showGuestLimitModal && usageStats && (
        <GuestLimitModal
          handsPlayed={usageStats.hands_played}
          handsLimit={usageStats.hands_limit}
          onReturnToMenu={() => {
            setShowGuestLimitModal(false);
            navigate('/menu');
          }}
        />
      )}

      {/* PWA Install Prompt */}
      <InstallPrompt />
    </>
  );
}

export default App;
