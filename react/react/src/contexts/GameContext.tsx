import React, { createContext, useContext, useState, useEffect } from 'react';
import type { ReactNode } from 'react';
import type { Socket } from 'socket.io-client';
import type { GameState, ChatMessage } from '../types';
import { useSocket } from '../hooks/useSocket';
import { useGameState } from '../hooks/useGameState';
import { gameAPI } from '../utils/api';

interface GameContextType {
  // State
  gameId: string | null;
  gameState: GameState | null;
  loading: boolean;
  error: string | null;
  socket: Socket | null;
  messages: ChatMessage[];
  aiThinking: boolean;
  playerPositions: Map<string, number>;
  
  // Actions
  createGame: (playerName: string) => Promise<void>;
  loadGame: (gameId: string) => Promise<void>;
  sendAction: (action: string, amount?: number) => Promise<void>;
  sendMessage: (message: string, sender: string) => Promise<void>;
  updateGameState: (newState: GameState) => void;
}

const GameContext = createContext<GameContextType | undefined>(undefined);

export function useGame() {
  const context = useContext(GameContext);
  if (!context) {
    throw new Error('useGame must be used within a GameProvider');
  }
  return context;
}

interface GameProviderProps {
  children: ReactNode;
}

export function GameProvider({ children }: GameProviderProps) {
  const [gameId, setGameId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [aiThinking, setAiThinking] = useState(false);
  const messageIdsRef = React.useRef<Set<string>>(new Set());
  
  const { socket } = useSocket();
  const { gameState, loading, error, playerPositions, fetchGameState, updateGameState } = useGameState(gameId);

  // Set up socket listeners
  useEffect(() => {
    if (!socket) return;

    socket.on('update_game_state', (data: { game_state: any }) => {
      console.log('Received game state update via WebSocket');
      const transformedState = {
        ...data.game_state,
        messages: data.game_state.messages || []
      };
      updateGameState(transformedState);
      
      // Update messages
      if (data.game_state.messages) {
        const newMessages = data.game_state.messages.filter((msg: ChatMessage) => {
          return !messageIdsRef.current.has(msg.id);
        });
        
        if (newMessages.length > 0) {
          newMessages.forEach((msg: ChatMessage) => messageIdsRef.current.add(msg.id));
          setMessages(prev => [...prev, ...newMessages]);
        }
      }
      
      // Update AI thinking state
      const currentPlayer = transformedState.players[transformedState.current_player_idx];
      setAiThinking(!currentPlayer.is_human && !currentPlayer.is_folded);
    });

    socket.on('player_turn_start', () => {
      setAiThinking(false);
    });

    socket.on('player_joined', (data: { message: string }) => {
      console.log('Player joined:', data.message);
    });

    return () => {
      socket.off('update_game_state');
      socket.off('player_turn_start');
      socket.off('player_joined');
    };
  }, [socket, updateGameState]);

  const createGame = async (playerName: string) => {
    try {
      const data = await gameAPI.createGame(playerName);
      setGameId(data.game_id);
      
      if (socket) {
        socket.emit('join_game', data.game_id);
      }
      
      await fetchGameState(data.game_id);
    } catch (err) {
      console.error('Failed to create game:', err);
      throw err;
    }
  };

  const loadGame = async (loadGameId: string) => {
    try {
      setGameId(loadGameId);
      
      if (socket) {
        socket.emit('join_game', loadGameId);
      }
      
      await fetchGameState(loadGameId);
    } catch (err) {
      console.error('Failed to load game:', err);
      throw err;
    }
  };

  const sendAction = async (action: string, amount?: number) => {
    if (!gameId) return;
    
    setAiThinking(true);
    
    try {
      await gameAPI.sendAction(gameId, action, amount);
      console.log('Action sent successfully, waiting for WebSocket updates');
    } catch (error) {
      console.error('Failed to send action:', error);
      setAiThinking(false);
      throw error;
    }
  };

  const sendMessage = async (message: string, sender: string) => {
    if (!gameId) return;
    
    try {
      await gameAPI.sendMessage(gameId, message, sender);
      // Refresh game state to get updated messages
      await fetchGameState(gameId);
    } catch (error) {
      console.error('Failed to send message:', error);
      throw error;
    }
  };

  const value: GameContextType = {
    gameId,
    gameState,
    loading,
    error,
    socket,
    messages,
    aiThinking,
    playerPositions,
    createGame,
    loadGame,
    sendAction,
    sendMessage,
    updateGameState,
  };

  return <GameContext.Provider value={value}>{children}</GameContext.Provider>;
}