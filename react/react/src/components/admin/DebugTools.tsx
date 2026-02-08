import { useState, useEffect, useCallback } from 'react';
import { config } from '../../config';
import { adminAPI } from '../../utils/api';
import './DebugTools.css';

// ============================================
// Types
// ============================================

type DebugTab = 'diagnostic' | 'psychology' | 'tilt' | 'memory' | 'elasticity' | 'pressure' | 'trajectory';

interface AlertState {
  type: 'success' | 'error' | 'info';
  message: string;
}

interface PlayerInfo {
  name: string;
  chips: number;
  is_human: boolean;
  is_active: boolean;
}

interface ActiveGame {
  game_id: string;
  owner_name: string;
  players: PlayerInfo[];
  phase: string | null;
  hand_number: number | null;
  is_active: boolean;  // true = in memory, false = saved only
}

interface DebugToolsProps {
  embedded?: boolean;
}

// ============================================
// Constants
// ============================================

const TABS: { id: DebugTab; label: string; endpoint: string }[] = [
  { id: 'diagnostic', label: 'Diagnostic', endpoint: 'diagnostic' },
  { id: 'psychology', label: 'Psychology', endpoint: 'psychology' },
  { id: 'tilt', label: 'Tilt System', endpoint: 'tilt-debug' },
  { id: 'memory', label: 'Memory', endpoint: 'memory-debug' },
  { id: 'elasticity', label: 'Elasticity', endpoint: 'elasticity' },
  { id: 'pressure', label: 'Pressure Stats', endpoint: 'pressure-stats' },
  { id: 'trajectory', label: 'Trajectory', endpoint: 'trajectory-viewer' },
];

// ============================================
// Main Component
// ============================================

export function DebugTools({ embedded = false }: DebugToolsProps) {
  const [gameId, setGameId] = useState('');
  const [activeTab, setActiveTab] = useState<DebugTab>('diagnostic');
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<unknown>(null);
  const [alert, setAlert] = useState<AlertState | null>(null);
  const [autoRefresh, setAutoRefresh] = useState(false);
  const [activeGames, setActiveGames] = useState<ActiveGame[]>([]);
  const [loadingGames, setLoadingGames] = useState(false);

  // Fetch active games
  const fetchActiveGames = useCallback(async () => {
    try {
      setLoadingGames(true);
      const response = await adminAPI.fetch('/admin/api/active-games');
      const data = await response.json();

      if (data.success) {
        setActiveGames(data.games);
        // If we don't have a game selected and there are games, select the first one
        if (!gameId && data.games.length > 0) {
          setGameId(data.games[0].game_id);
        }
      }
    } catch {
      // Failed to fetch active games - silently ignore
    } finally {
      setLoadingGames(false);
    }
  }, [gameId]);

  // Fetch active games on mount and periodically
  useEffect(() => {
    fetchActiveGames();
    const interval = setInterval(fetchActiveGames, 10000); // Refresh every 10 seconds
    return () => clearInterval(interval);
  }, [fetchActiveGames]);

  // Fetch debug data
  const fetchData = useCallback(async () => {
    if (!gameId.trim()) {
      setAlert({ type: 'error', message: 'Please enter a game ID' });
      return;
    }

    const tab = TABS.find(t => t.id === activeTab);
    if (!tab) return;

    try {
      setLoading(true);
      const response = await fetch(`${config.API_URL}/api/game/${gameId}/${tab.endpoint}`);
      const data = await response.json();

      if (response.ok) {
        setResult(data);
        setAlert(null);
      } else {
        setResult(null);
        setAlert({ type: 'error', message: data.error || 'Failed to fetch data' });
      }
    } catch {
      setResult(null);
      setAlert({ type: 'error', message: 'Failed to connect to server' });
    } finally {
      setLoading(false);
    }
  }, [gameId, activeTab]);

  // Auto-refresh
  useEffect(() => {
    let interval: ReturnType<typeof setInterval> | undefined;

    if (autoRefresh && gameId.trim()) {
      interval = setInterval(fetchData, 5000);
    }

    return () => {
      if (interval) clearInterval(interval);
    };
  }, [autoRefresh, gameId, fetchData]);

  // Clear result when tab changes
  useEffect(() => {
    setResult(null);
  }, [activeTab]);

  // Clear alert after timeout
  useEffect(() => {
    if (alert) {
      const timer = setTimeout(() => setAlert(null), 3000);
      return () => clearTimeout(timer);
    }
  }, [alert]);

  return (
    <div className={`dt-container ${embedded ? 'dt-container--embedded' : ''}`}>
      {/* Alert Toast */}
      {alert && (
        <div className={`dt-alert dt-alert--${alert.type}`}>
          <span className="dt-alert__icon">
            {alert.type === 'success' ? '✓' : alert.type === 'error' ? '✕' : 'i'}
          </span>
          <span className="dt-alert__message">{alert.message}</span>
          <button className="dt-alert__close" onClick={() => setAlert(null)}>×</button>
        </div>
      )}

      {/* Header */}
      <div className="dt-header">
        <h2 className="dt-header__title">Debug Tools</h2>
        <p className="dt-header__subtitle">Inspect game state and AI system internals</p>
      </div>

      {/* Game Selection */}
      <div className="dt-game-selection">
        {/* Active Games Dropdown */}
        <div className="dt-select-group">
          <label className="dt-select-label">Active Games</label>
          <div className="dt-select-wrapper">
            <select
              className="dt-select"
              value={gameId}
              onChange={(e) => setGameId(e.target.value)}
              disabled={loadingGames}
            >
              <option value="">
                {loadingGames ? 'Loading...' : activeGames.length === 0 ? 'No active games' : 'Select a game...'}
              </option>
              {activeGames.map(game => {
                const humanPlayer = game.players.find(p => p.is_human);
                const playerName = humanPlayer?.name || game.owner_name || 'Unknown';
                const aiCount = game.players.filter(p => !p.is_human).length;
                const phaseLabel = game.phase ? ` - ${game.phase}` : '';
                const statusIcon = game.is_active ? '🟢' : '💾';
                return (
                  <option key={game.game_id} value={game.game_id}>
                    {statusIcon} {playerName} vs {aiCount} AI{aiCount !== 1 ? 's' : ''}{phaseLabel}
                  </option>
                );
              })}
            </select>
            <button
              className="dt-btn dt-btn--icon"
              onClick={fetchActiveGames}
              disabled={loadingGames}
              title="Refresh game list"
              type="button"
            >
              ↻
            </button>
          </div>
        </div>

        {/* Manual Game ID Input */}
        <div className="dt-input-group">
          <label className="dt-input-label">Or enter Game ID</label>
          <input
            type="text"
            className="dt-input"
            value={gameId}
            onChange={(e) => setGameId(e.target.value)}
            placeholder="Paste game ID..."
            onKeyDown={(e) => e.key === 'Enter' && fetchData()}
          />
        </div>
      </div>

      {/* Fetch Controls */}
      <div className="dt-controls">
        <button
          className="dt-btn dt-btn--primary"
          onClick={fetchData}
          disabled={loading || !gameId.trim()}
        >
          {loading ? 'Loading...' : 'Fetch Data'}
        </button>
        <label className="dt-checkbox">
          <input
            type="checkbox"
            checked={autoRefresh}
            onChange={(e) => setAutoRefresh(e.target.checked)}
          />
          <span>Auto-refresh (5s)</span>
        </label>
      </div>

      {/* Debug Tabs */}
      <div className="dt-tabs">
        {TABS.map(tab => (
          <button
            key={tab.id}
            className={`dt-tab ${activeTab === tab.id ? 'dt-tab--active' : ''}`}
            onClick={() => setActiveTab(tab.id)}
            type="button"
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* Result Display */}
      <div className="dt-result">
        {activeTab === 'trajectory' ? (
          gameId.trim() ? (
            <iframe
              src={`${config.API_URL}/api/game/${gameId}/trajectory-viewer`}
              style={{ width: '100%', height: '800px', border: 'none', borderRadius: '8px' }}
              title="Psychology Trajectory Viewer"
            />
          ) : (
            <div className="dt-result__empty">
              Select a game to view psychology trajectories
            </div>
          )
        ) : loading ? (
          <div className="dt-result__loading">
            <div className="dt-loading__spinner" />
            <span>Loading...</span>
          </div>
        ) : result ? (
          <pre className="dt-result__json">{JSON.stringify(result, null, 2)}</pre>
        ) : (
          <div className="dt-result__empty">
            Enter a game ID and click Fetch to view debug data
          </div>
        )}
      </div>

      {/* Endpoint Info */}
      <div className="dt-info">
        <div className="dt-info__title">API Endpoint</div>
        <code className="dt-info__code">
          GET /api/game/{'{game_id}'}/{TABS.find(t => t.id === activeTab)?.endpoint}
        </code>
      </div>
    </div>
  );
}

export default DebugTools;
