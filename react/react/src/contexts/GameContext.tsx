import React, { createContext, useContext, useState, useEffect, useCallback } from 'react';
import type { ReactNode } from 'react';
import type { Socket } from 'socket.io-client';
import type { GameState, ChatMessage, BackendChatMessage } from '../types';
import { useSocket } from '../hooks/useSocket';
import { useGameState } from '../hooks/useGameState';
import { gameAPI } from '../utils/api';
import { logger } from '../utils/logger';

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

// Cap message arrays to prevent unbounded memory growth in long games
const MAX_MESSAGES = 200;

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

  // Helper to append only new messages (deduplicates by ID)
  const appendNewMessages = useCallback((incomingMessages: ChatMessage[]) => {
    const newMessages = incomingMessages.filter(msg => !messageIdsRef.current.has(msg.id));
    if (newMessages.length > 0) {
      newMessages.forEach(msg => messageIdsRef.current.add(msg.id));
      setMessages(prev => [...prev, ...newMessages].slice(-MAX_MESSAGES));
    }
  }, []);

  // Set up socket listeners
  useEffect(() => {
    if (!socket) return;

    socket.on('update_game_state', (data: { game_state: any }) => {

      const transformedState = {
        ...data.game_state,
        messages: data.game_state.messages || []
      };
      updateGameState(transformedState);

      if (data.game_state.messages) {
        appendNewMessages(data.game_state.messages);
      }

      // Update AI thinking state
      const currentPlayer = transformedState.players[transformedState.current_player_idx];
      setAiThinking(!currentPlayer.is_human && !currentPlayer.is_folded);
    });

    socket.on('player_turn_start', () => {
      setAiThinking(false);
    });

    socket.on('player_joined', (_data: { message: string }) => {
      // Placeholder for future player join handling
    });

    // Listen for new message (emitted by send_message in backend)
    socket.on('new_message', (data: { message: BackendChatMessage }) => {

      const msg = data.message;

      const transformedMessage: ChatMessage = {
        id: msg.id || `msg-${Date.now()}`,
        sender: msg.sender,
        message: msg.content,
        timestamp: msg.timestamp,
        type: msg.message_type,
        ...(msg.action && { action: msg.action }),
        ...(msg.phase && { phase: msg.phase }),
        ...(msg.cards && { cards: msg.cards }),
        ...(msg.win_result && { win_result: msg.win_result }),
      };

      appendNewMessages([transformedMessage]);
    });

    return () => {
      socket.off('update_game_state');
      socket.off('player_turn_start');
      socket.off('player_joined');
      socket.off('new_message');
    };
  }, [socket, updateGameState, appendNewMessages]);

  const createGame = async (playerName: string) => {
    try {
      const data = await gameAPI.createGame(playerName);
      setGameId(data.game_id);
      
      if (socket) {
        socket.emit('join_game', data.game_id);
      }
      
      await fetchGameState(data.game_id);
    } catch (err) {
      logger.error('Failed to create game:', err);
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
      logger.error('Failed to load game:', err);
      throw err;
    }
  };

  const sendAction = async (action: string, amount?: number) => {
    if (!gameId) return;
    
    setAiThinking(true);
    
    try {
      await gameAPI.sendAction(gameId, action, amount);

    } catch (error) {
      logger.error('Failed to send action:', error);
      setAiThinking(false);
      throw error;
    }
  };

  const sendMessage = async (message: string, sender: string) => {
    if (!gameId) return;

    try {
      await gameAPI.sendMessage(gameId, message, sender);
      // Message will be received via WebSocket 'new_message' event
    } catch (error) {
      logger.error('Failed to send message:', error);
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