/**
 * LiveMonitoringView - Main container for real-time experiment monitoring
 *
 * Displays a full-screen overlay with a grid of running games and
 * supports player drill-down for detailed psychology and LLM stats.
 */

import { useState, useEffect, useCallback } from 'react';
import { ArrowLeft, RefreshCw, Monitor, Loader2, XCircle, LayoutGrid, Table2 } from 'lucide-react';
import { config } from '../../../../config';
import { GameMonitorGrid } from './GameMonitorGrid';
import { GameMonitorTable } from './GameMonitorTable';
import { PlayerDrilldownPanel } from './PlayerDrilldownPanel';
import type { GameSnapshot, LiveGamesResponse, SelectedPlayer } from './types';
import './LiveMonitoringView.css';

type ViewMode = 'cards' | 'table';

interface LiveMonitoringViewProps {
  experimentId: number;
  experimentName: string;
  onClose: () => void;
}

export function LiveMonitoringView({
  experimentId,
  experimentName,
  onClose,
}: LiveMonitoringViewProps) {
  const [games, setGames] = useState<GameSnapshot[]>([]);
  const [experimentStatus, setExperimentStatus] = useState<string>('running');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedPlayer, setSelectedPlayer] = useState<SelectedPlayer | null>(null);
  const [lastUpdate, setLastUpdate] = useState<Date>(new Date());
  const [viewMode, setViewMode] = useState<ViewMode>('cards');

  const fetchLiveGames = useCallback(async (signal?: AbortSignal): Promise<void> => {
    try {
      const response = await fetch(
        `${config.API_URL}/api/experiments/${experimentId}/live-games`,
        { signal }
      );

      // Don't update state if request was aborted
      if (signal?.aborted) return;

      const data: LiveGamesResponse = await response.json();

      if (data.success) {
        setGames(data.games);
        setExperimentStatus(data.experiment_status);
        setError(null);
        setLastUpdate(new Date());
      } else {
        setError(data.error || 'Failed to load live games');
      }
    } catch (err) {
      // Ignore abort errors - these are expected on unmount
      if (err instanceof Error && err.name === 'AbortError') return;
      console.error('Failed to fetch live games:', err);
      setError('Failed to connect to server');
    } finally {
      setLoading(false);
    }
  }, [experimentId]);

  // Initial load with cleanup
  useEffect(() => {
    const abortController = new AbortController();
    fetchLiveGames(abortController.signal);
    return () => abortController.abort();
  }, [fetchLiveGames]);

  // Poll every 5 seconds while experiment is running
  useEffect(() => {
    if (experimentStatus !== 'running') return;

    const abortController = new AbortController();
    const interval = setInterval(() => fetchLiveGames(abortController.signal), 5000);
    return () => {
      clearInterval(interval);
      abortController.abort();
    };
  }, [experimentStatus, fetchLiveGames]);

  // Handle ESC key to close
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        if (selectedPlayer) {
          setSelectedPlayer(null);
        } else {
          onClose();
        }
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [selectedPlayer, onClose]);

  const handlePlayerClick = (gameId: string, playerName: string) => {
    setSelectedPlayer({ gameId, playerName });
  };

  const handleClosePanel = () => {
    setSelectedPlayer(null);
  };

  const getStatusIndicator = () => {
    switch (experimentStatus) {
      case 'running':
        return (
          <span className="live-monitor__status live-monitor__status--running">
            <span className="live-monitor__status-dot" />
            Running
          </span>
        );
      case 'completed':
        return (
          <span className="live-monitor__status live-monitor__status--completed">
            Completed
          </span>
        );
      case 'failed':
        return (
          <span className="live-monitor__status live-monitor__status--failed">
            Failed
          </span>
        );
      default:
        return (
          <span className="live-monitor__status">
            {experimentStatus}
          </span>
        );
    }
  };

  if (loading) {
    return (
      <div className="live-monitor">
        <div className="live-monitor__loading">
          <Loader2 size={32} className="animate-spin" />
          <span>Loading live games...</span>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="live-monitor">
        <div className="live-monitor__error">
          <XCircle size={32} />
          <span>{error}</span>
          <button onClick={() => fetchLiveGames()} type="button">
            Retry
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="live-monitor">
      {/* Header */}
      <header className="live-monitor__header">
        <div className="live-monitor__header-left">
          <button
            className="live-monitor__back-btn"
            onClick={onClose}
            type="button"
            title="Back to Experiment"
          >
            <ArrowLeft size={20} />
            <span>Back</span>
          </button>
          <div className="live-monitor__title">
            <Monitor size={20} />
            <h1>Live Monitor: {experimentName}</h1>
          </div>
        </div>
        <div className="live-monitor__header-right">
          <div className="live-monitor__view-toggle">
            <button
              className={`live-monitor__view-btn ${viewMode === 'cards' ? 'live-monitor__view-btn--active' : ''}`}
              onClick={() => setViewMode('cards')}
              type="button"
              title="Card View"
            >
              <LayoutGrid size={16} />
            </button>
            <button
              className={`live-monitor__view-btn ${viewMode === 'table' ? 'live-monitor__view-btn--active' : ''}`}
              onClick={() => setViewMode('table')}
              type="button"
              title="Table View"
            >
              <Table2 size={16} />
            </button>
          </div>
          {getStatusIndicator()}
          <button
            className="live-monitor__refresh-btn"
            onClick={() => fetchLiveGames()}
            type="button"
            title="Refresh"
          >
            <RefreshCw size={16} />
            <span className="live-monitor__refresh-interval">5s</span>
          </button>
          <span className="live-monitor__last-update">
            Updated: {lastUpdate.toLocaleTimeString()}
          </span>
        </div>
      </header>

      {/* Main Content */}
      <main className={`live-monitor__content ${selectedPlayer ? 'live-monitor__content--dimmed' : ''}`}>
        {games.length === 0 ? (
          <div className="live-monitor__empty">
            <Monitor size={48} />
            <h2>No games running</h2>
            <p>
              {experimentStatus === 'running'
                ? 'Waiting for tournaments to start...'
                : 'This experiment has finished.'}
            </p>
          </div>
        ) : viewMode === 'cards' ? (
          <GameMonitorGrid
            games={games}
            onPlayerClick={handlePlayerClick}
          />
        ) : (
          <GameMonitorTable
            games={games}
            onPlayerClick={handlePlayerClick}
          />
        )}
      </main>

      {/* Player Drill-down Panel */}
      {selectedPlayer && (
        <PlayerDrilldownPanel
          experimentId={experimentId}
          gameId={selectedPlayer.gameId}
          playerName={selectedPlayer.playerName}
          onClose={handleClosePanel}
        />
      )}
    </div>
  );
}
