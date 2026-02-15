/**
 * HandReplayBrowser - Entry point for hand replay
 *
 * Provides game ID input and hand selector dropdown.
 * Fetches hand list and replay data from the API.
 */

import { useState, useCallback } from 'react';
import { Search, Film, Loader2 } from 'lucide-react';
import { HandReplayViewer } from './HandReplayViewer';
import { config } from '../../../config';
import type { HandReplayData, HandListItem } from './types';
import './HandReplay.css';

export function HandReplayBrowser() {
  const [gameId, setGameId] = useState('');
  const [handList, setHandList] = useState<HandListItem[]>([]);
  const [selectedHand, setSelectedHand] = useState<number | null>(null);
  const [replayData, setReplayData] = useState<HandReplayData | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchHands = useCallback(async () => {
    const trimmed = gameId.trim();
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
  }, [gameId]);

  const fetchReplay = useCallback(async (handNumber: number) => {
    const trimmed = gameId.trim();
    if (!trimmed) return;

    setLoading(true);
    setError(null);
    setSelectedHand(handNumber);
    setReplayData(null);

    try {
      const res = await fetch(
        `${config.API_URL}/admin/api/hands/${encodeURIComponent(trimmed)}/${handNumber}/replay`,
        { credentials: 'include' },
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
      replay.players = replay.players.map((p: { name: string; starting_stack: number; position: string; is_human: boolean }, i: number) => ({
        ...p,
        seat_index: i,
        hole_cards: holeCards[p.name] ?? null,
      }));
      setReplayData(replay);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to fetch replay');
    } finally {
      setLoading(false);
    }
  }, [gameId]);

  const handleKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (e.key === 'Enter') {
      fetchHands();
    }
  }, [fetchHands]);

  const handleHandSelect = useCallback((e: React.ChangeEvent<HTMLSelectElement>) => {
    const num = parseInt(e.target.value, 10);
    if (!isNaN(num)) {
      fetchReplay(num);
    }
  }, [fetchReplay]);

  return (
    <div className="hand-replay-browser">
      {/* Search bar */}
      <div className="hand-replay-browser__controls">
        <div className="hand-replay-browser__search">
          <Search size={16} className="hand-replay-browser__search-icon" />
          <input
            type="text"
            className="hand-replay-browser__input"
            placeholder="Enter Game ID..."
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
                  Hand #{h.hand_number} — {h.player_count}p — {h.winner_names.join(', ') || 'No winner'}
                </option>
              ))}
            </select>
          </div>
        )}
      </div>

      {/* Error */}
      {error && (
        <div className="hand-replay-browser__error">{error}</div>
      )}

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
          <p>Enter a Game ID to browse and replay recorded hands.</p>
        </div>
      )}
    </div>
  );
}
