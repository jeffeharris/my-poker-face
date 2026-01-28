import { useState, useEffect } from 'react';
import type { GameState, Player } from '../types';
import { config } from '../config';
import { logger } from '../utils/logger';

// Cache key prefix for localStorage
const GAME_STATE_CACHE_PREFIX = 'gameStateCache_';

// Helper to get cached game state
function getCachedGameState(gameId: string): GameState | null {
  try {
    const cached = localStorage.getItem(GAME_STATE_CACHE_PREFIX + gameId);
    if (cached) {
      return JSON.parse(cached);
    }
  } catch (e) {
    logger.warn('Failed to parse cached game state:', e);
  }
  return null;
}

// Helper to cache game state
function cacheGameState(gameId: string, state: GameState): void {
  try {
    localStorage.setItem(GAME_STATE_CACHE_PREFIX + gameId, JSON.stringify(state));
  } catch (e) {
    logger.warn('Failed to cache game state:', e);
  }
}

interface UseGameStateResult {
  gameState: GameState | null;
  loading: boolean;
  error: string | null;
  playerPositions: Map<string, number>;
  fetchGameState: (gameId: string) => Promise<void>;
  updateGameState: (newState: GameState) => void;
}

export function useGameState(gameId: string | null): UseGameStateResult {
  // Initialize with cached state if available
  const [gameState, setGameState] = useState<GameState | null>(() => {
    if (gameId) {
      return getCachedGameState(gameId);
    }
    return null;
  });
  // If we have cached state, don't show loading spinner
  const [loading, setLoading] = useState(() => {
    if (gameId) {
      return getCachedGameState(gameId) === null;
    }
    return true;
  });
  const [error, setError] = useState<string | null>(null);
  const [playerPositions, setPlayerPositions] = useState<Map<string, number>>(() => new Map());

  const initializePlayerPositions = (players: Player[]) => {
    const positions = new Map<string, number>();
    const humanIndex = players.findIndex((p: Player) => p.is_human);
    let positionIndex = 0;
    
    // Assign human player to position 0 (bottom)
    if (humanIndex !== -1) {
      positions.set(players[humanIndex].name, 0);
      positionIndex = 1;
    }
    
    // Assign other players to remaining positions
    players.forEach((player: Player) => {
      if (!player.is_human) {
        positions.set(player.name, positionIndex);
        positionIndex++;
      }
    });
    
    setPlayerPositions(positions);
  };

  const fetchGameState = async (gId: string) => {
    try {
      // Only show loading if we don't have cached state
      const hasCached = getCachedGameState(gId) !== null;
      if (!hasCached) {
        setLoading(true);
      }
      setError(null);

      const response = await fetch(`${config.API_URL}/api/game-state/${gId}`);
      if (!response.ok) {
        throw new Error('Failed to load game');
      }

      const data = await response.json();

      // Check if it's an error response
      if (data.error || !data.players || data.players.length === 0) {
        throw new Error(data.message || 'Invalid game state');
      }

      setGameState(data);
      // Cache the fresh state
      cacheGameState(gId, data);

      // Initialize positions only if they haven't been set
      if (playerPositions.size === 0) {
        initializePlayerPositions(data.players);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to fetch game state');
      throw err;
    } finally {
      setLoading(false);
    }
  };

  const updateGameState = (newState: GameState) => {
    setGameState(newState);
    // Cache on every update
    if (gameId) {
      cacheGameState(gameId, newState);
    }

    // Initialize positions if needed
    if (playerPositions.size === 0 && newState.players.length > 0) {
      initializePlayerPositions(newState.players);
    }
  };

  // Initialize player positions from cached state on mount
  useEffect(() => {
    if (gameId) {
      const cached = getCachedGameState(gameId);
      if (cached && playerPositions.size === 0) {
        initializePlayerPositions(cached.players);
      }
      // Always fetch fresh data (will update cache)
      fetchGameState(gameId);
    }
  }, [gameId]);

  return {
    gameState,
    loading,
    error,
    playerPositions,
    fetchGameState,
    updateGameState,
  };
}