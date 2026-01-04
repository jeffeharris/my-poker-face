import { useState, useEffect } from 'react';
import type { GameState, Player } from '../types';
import { config } from '../config';

interface UseGameStateResult {
  gameState: GameState | null;
  loading: boolean;
  error: string | null;
  playerPositions: Map<string, number>;
  fetchGameState: (gameId: string) => Promise<void>;
  updateGameState: (newState: GameState) => void;
}

export function useGameState(gameId: string | null): UseGameStateResult {
  const [gameState, setGameState] = useState<GameState | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [playerPositions, setPlayerPositions] = useState<Map<string, number>>(new Map());

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
      setLoading(true);
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
    
    // Initialize positions if needed
    if (playerPositions.size === 0 && newState.players.length > 0) {
      initializePlayerPositions(newState.players);
    }
  };

  useEffect(() => {
    if (gameId) {
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