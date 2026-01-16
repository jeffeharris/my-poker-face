import { useState, useEffect } from 'react';
import { config } from '../../config';
import './DebugTools.css';

// ============================================
// Types
// ============================================

type DebugTab = 'diagnostic' | 'tilt' | 'memory' | 'elasticity' | 'pressure';

interface AlertState {
  type: 'success' | 'error' | 'info';
  message: string;
}

interface DebugToolsProps {
  embedded?: boolean;
}

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

  const TABS: { id: DebugTab; label: string; endpoint: string }[] = [
    { id: 'diagnostic', label: 'Diagnostic', endpoint: 'diagnostic' },
    { id: 'tilt', label: 'Tilt System', endpoint: 'tilt-debug' },
    { id: 'memory', label: 'Memory', endpoint: 'memory-debug' },
    { id: 'elasticity', label: 'Elasticity', endpoint: 'elasticity' },
    { id: 'pressure', label: 'Pressure Stats', endpoint: 'pressure-stats' },
  ];

  // Fetch debug data
  const fetchData = async () => {
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
    } catch (error) {
      setResult(null);
      setAlert({ type: 'error', message: 'Failed to connect to server' });
    } finally {
      setLoading(false);
    }
  };

  // Auto-refresh
  useEffect(() => {
    let interval: ReturnType<typeof setInterval> | undefined;

    if (autoRefresh && gameId.trim()) {
      interval = setInterval(fetchData, 5000);
    }

    return () => {
      if (interval) clearInterval(interval);
    };
  }, [autoRefresh, gameId, activeTab]);

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

      {/* Game ID Input */}
      <div className="dt-input-row">
        <input
          type="text"
          className="dt-input"
          value={gameId}
          onChange={(e) => setGameId(e.target.value)}
          placeholder="Enter Game ID..."
          onKeyDown={(e) => e.key === 'Enter' && fetchData()}
        />
        <button
          className="dt-btn dt-btn--primary"
          onClick={fetchData}
          disabled={loading || !gameId.trim()}
        >
          {loading ? 'Loading...' : 'Fetch'}
        </button>
        <label className="dt-checkbox">
          <input
            type="checkbox"
            checked={autoRefresh}
            onChange={(e) => setAutoRefresh(e.target.checked)}
          />
          <span>Auto-refresh</span>
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
        {loading ? (
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
