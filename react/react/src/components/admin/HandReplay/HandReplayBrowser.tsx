/**
 * HandReplayBrowser - Entry point for hand replay
 *
 * Provides game ID input and hand selector dropdown.
 * Fetches hand list and replay data from the API.
 */

import { useState, useCallback, useEffect } from 'react';
import { Search, Film, Loader2, RefreshCw } from 'lucide-react';
import { HandReplayViewer } from './HandReplayViewer';
import { config } from '../../../config';
import { adminAPI } from '../../../utils/api';
import type { HandReplayData, HandListItem } from './types';
import './HandReplay.css';

interface GamePlayerInfo {
  name: string;
  is_human: boolean;
}

interface GameListEntry {
  game_id: string;
  owner_name: string;
  players: GamePlayerInfo[];
  phase: string | null;
  hand_number: number | null;
  is_active: boolean;
}

// Hand Replay is inherently about browsing historical games, so request a
// larger window than the default 20 saved games the endpoint returns.
const GAME_LIST_LIMIT = 100;

export function HandReplayBrowser() {
  const [gameId, setGameId] = useState('');
  const [handList, setHandList] = useState<HandListItem[]>([]);
  const [selectedHand, setSelectedHand] = useState<number | null>(null);
  const [replayData, setReplayData] = useState<HandReplayData | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [games, setGames] = useState<GameListEntry[]>([]);
  const [loadingGames, setLoadingGames] = useState(false);

  const fetchHandsFor = useCallback(async (rawGameId: string) => {
    const trimmed = rawGameId.trim();
    if (!trimmed) return;

    setLoading(true);
    setError(null);
    setHandList([]);
    setSelectedHand(null);
    setReplayData(null);

    try {
      const res = await fetch(`${config.API_URL}/admin/api/hands/${encodeURIComponent(trimmed)}`, {
        credentials: 'include',
      });
      if (!res.ok) {
        throw new Error(`Failed to fetch hands: ${res.status}`);
      }
      const json = await res.json();
      if (!json.success) {
        throw new Error(json.error ?? 'Unknown error');
      }
      setHandList(json.hands ?? []);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to fetch hands');
    } finally {
      setLoading(false);
    }
  }, []);

  const fetchHands = useCallback(() => fetchHandsFor(gameId), [fetchHandsFor, gameId]);

  const fetchGames = useCallback(async () => {
    setLoadingGames(true);
    try {
      const res = await adminAPI.fetch(`/admin/api/active-games?limit=${GAME_LIST_LIMIT}`);
      const json = await res.json();
      if (json.success) {
        setGames(json.games ?? []);
      }
    } catch {
      // Silent — manual input still works as a fallback.
    } finally {
      setLoadingGames(false);
    }
  }, []);

  useEffect(() => {
    fetchGames();
  }, [fetchGames]);

  const handleGameSelect = useCallback(
    (e: React.ChangeEvent<HTMLSelectElement>) => {
      const selected = e.target.value;
      setGameId(selected);
      if (selected) {
        fetchHandsFor(selected);
      }
    },
    [fetchHandsFor]
  );

  const fetchReplay = useCallback(
    async (handNumber: number) => {
      const trimmed = gameId.trim();
      if (!trimmed) return;

      setLoading(true);
      setError(null);
      setSelectedHand(handNumber);
      setReplayData(null);

      try {
        const res = await fetch(
          `${config.API_URL}/admin/api/hands/${encodeURIComponent(trimmed)}/${handNumber}/replay`,
          { credentials: 'include' }
        );
        if (!res.ok) {
          throw new Error(`Failed to fetch replay: ${res.status}`);
        }
        const json = await res.json();
        if (!json.success) {
          throw new Error(json.error ?? 'Unknown error');
        }
        // Transform API response: add seat_index and hole_cards to players
        const replay = json.replay;
        const holeCards: Record<string, string[]> = replay.hole_cards ?? {};
        replay.players = replay.players.map(
          (
            p: { name: string; starting_stack: number; position: string; is_human: boolean },
            i: number
          ) => ({
            ...p,
            seat_index: i,
            hole_cards: holeCards[p.name] ?? null,
          })
        );
        setReplayData(replay);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to fetch replay');
      } finally {
        setLoading(false);
      }
    },
    [gameId]
  );

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === 'Enter') {
        fetchHands();
      }
    },
    [fetchHands]
  );

  const handleHandSelect = useCallback(
    (e: React.ChangeEvent<HTMLSelectElement>) => {
      const num = parseInt(e.target.value, 10);
      if (!isNaN(num)) {
        fetchReplay(num);
      }
    },
    [fetchReplay]
  );

  return (
    <div className="hand-replay-browser">
      {/* Game picker + manual ID input */}
      <div className="hand-replay-browser__controls">
        <div className="hand-replay-browser__picker">
          <label className="hand-replay-browser__picker-label">Game</label>
          <select
            className="hand-replay-browser__select themed-select"
            value={games.some((g) => g.game_id === gameId) ? gameId : ''}
            onChange={handleGameSelect}
            disabled={loadingGames}
          >
            <option value="">
              {loadingGames
                ? 'Loading…'
                : games.length === 0
                  ? 'No games found'
                  : `Select a game (${games.length})`}
            </option>
            {games.map((game) => {
              const human = game.players.find((p) => p.is_human);
              const playerName = human?.name || game.owner_name || 'Unknown';
              const aiCount = game.players.filter((p) => !p.is_human).length;
              const phaseLabel = game.phase ? ` — ${game.phase}` : '';
              const statusIcon = game.is_active ? '🟢' : '💾';
              return (
                <option key={game.game_id} value={game.game_id}>
                  {statusIcon} {playerName} vs {aiCount} AI{aiCount !== 1 ? 's' : ''}
                  {phaseLabel}
                </option>
              );
            })}
          </select>
          <button
            type="button"
            className="hand-replay-browser__refresh-btn"
            onClick={fetchGames}
            disabled={loadingGames}
            aria-label="Refresh game list"
            title="Refresh game list"
          >
            <RefreshCw size={14} className={loadingGames ? 'animate-spin' : undefined} />
          </button>
        </div>

        <div className="hand-replay-browser__search">
          <Search size={16} className="hand-replay-browser__search-icon" />
          <input
            type="text"
            className="hand-replay-browser__input"
            placeholder="…or paste a Game ID"
            value={gameId}
            onChange={(e) => setGameId(e.target.value)}
            onKeyDown={handleKeyDown}
          />
          <button
            className="hand-replay-browser__fetch-btn"
            onClick={fetchHands}
            disabled={loading || !gameId.trim()}
          >
            {loading ? <Loader2 size={14} className="animate-spin" /> : <Film size={14} />}
            Load Hands
          </button>
        </div>

        {/* Hand selector */}
        {handList.length > 0 && (
          <div className="hand-replay-browser__selector">
            <select
              className="themed-select"
              value={selectedHand ?? ''}
              onChange={handleHandSelect}
            >
              <option value="" disabled>
                Select a hand ({handList.length} available)
              </option>
              {handList.map((h) => (
                <option key={h.hand_number} value={h.hand_number}>
                  Hand #{h.hand_number} — {h.player_count}p —{' '}
                  {h.winner_names.join(', ') || 'No winner'}
                </option>
              ))}
            </select>
          </div>
        )}
      </div>

      {/* Error */}
      {error && <div className="hand-replay-browser__error">{error}</div>}

      {/* Loading state */}
      {loading && !replayData && (
        <div className="hand-replay-browser__loading">
          <Loader2 size={24} className="animate-spin" />
          <span>Loading...</span>
        </div>
      )}

      {/* Replay viewer */}
      {replayData && <HandReplayViewer data={replayData} />}

      {/* Empty state */}
      {!loading && !replayData && !error && handList.length === 0 && (
        <div className="hand-replay-browser__empty">
          <Film size={48} />
          <h3>Hand Replay</h3>
          <p>Pick a game above to browse and replay recorded hands.</p>
        </div>
      )}
    </div>
  );
}
