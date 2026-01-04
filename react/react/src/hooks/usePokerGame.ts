import { useEffect, useState, useRef, useCallback } from 'react';
import { io, Socket } from 'socket.io-client';
import type { ChatMessage, GameState } from '../types';
import type { TournamentResult, EliminationEvent } from '../types/tournament';
import { config } from '../config';

interface UsePokerGameOptions {
  gameId: string | null;
  playerName?: string;
  onGameCreated?: (gameId: string) => void;
  onNewAiMessage?: (message: ChatMessage) => void;
  onGameLoadFailed?: () => void;
}

interface UsePokerGameResult {
  gameState: GameState | null;
  loading: boolean;
  error: string | null;
  gameId: string | null;
  messages: ChatMessage[];
  aiThinking: boolean;
  winnerInfo: any;
  tournamentResult: TournamentResult | null;
  eliminationEvents: EliminationEvent[];
  socketRef: React.MutableRefObject<Socket | null>;
  handlePlayerAction: (action: string, amount?: number) => Promise<void>;
  handleSendMessage: (message: string) => Promise<void>;
  clearWinnerInfo: () => void;
  clearTournamentResult: () => void;
  refreshGameState: (gId: string) => Promise<boolean>;
}

const fetchWithCredentials = (url: string, options: RequestInit = {}) => {
  return fetch(url, {
    ...options,
    credentials: 'include',
  });
};

export function usePokerGame({
  gameId: providedGameId,
  playerName,
  onGameCreated,
  onNewAiMessage,
  onGameLoadFailed,
}: UsePokerGameOptions): UsePokerGameResult {
  const [gameState, setGameState] = useState<GameState | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [gameId, setGameId] = useState<string | null>(null);
  const [aiThinking, setAiThinking] = useState(false);
  const socketRef = useRef<Socket | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const messageIdsRef = useRef<Set<string>>(new Set());
  const [winnerInfo, setWinnerInfo] = useState<any>(null);
  const [tournamentResult, setTournamentResult] = useState<TournamentResult | null>(null);
  const [eliminationEvents, setEliminationEvents] = useState<EliminationEvent[]>([]);
  const isInitialConnectionRef = useRef(true); // Track if this is first connection vs reconnect

  const clearWinnerInfo = useCallback(() => setWinnerInfo(null), []);
  const clearTournamentResult = useCallback(() => setTournamentResult(null), []);

  const setupSocketListeners = useCallback((socket: Socket) => {
    socket.on('disconnect', () => {
      console.log('WebSocket disconnected');
    });

    socket.on('player_joined', (data: { message: string }) => {
      console.log('Player joined:', data.message);
    });

    socket.on('update_game_state', (data: { game_state: any }) => {
      console.log('Received game state update via WebSocket');
      const transformedState = {
        ...data.game_state,
        messages: data.game_state.messages || []
      };
      setGameState(transformedState);

      if (data.game_state.messages) {
        const newMessages = data.game_state.messages.filter((msg: ChatMessage) => {
          return !messageIdsRef.current.has(msg.id);
        });

        if (newMessages.length > 0) {
          newMessages.forEach((msg: ChatMessage) => messageIdsRef.current.add(msg.id));
          setMessages(prev => [...prev, ...newMessages]);

          // Notify about new AI messages (for mobile floating bubbles)
          if (onNewAiMessage) {
            const aiMessages = newMessages.filter((msg: ChatMessage) => msg.type === 'ai');
            if (aiMessages.length > 0) {
              onNewAiMessage(aiMessages[aiMessages.length - 1]);
            }
          }
        }
      }

      const currentPlayer = transformedState.players[transformedState.current_player_idx];
      setAiThinking(!currentPlayer.is_human && !currentPlayer.is_folded);
    });

    // Listen for new message (singular - desktop format)
    socket.on('new_message', (data: { message: any }) => {
      console.log('Received new_message via WebSocket');
      const msg = data.message;
      const msgId = msg.id || String(msg.timestamp);

      if (messageIdsRef.current.has(msgId)) {
        return;
      }

      const transformedMessage: ChatMessage = {
        id: msgId,
        sender: msg.sender,
        message: msg.content,
        timestamp: msg.timestamp,
        type: msg.message_type,
        action: msg.action  // Include action for AI messages
      };

      messageIdsRef.current.add(msgId);
      setMessages(prev => [...prev, transformedMessage]);

      if (onNewAiMessage && transformedMessage.type === 'ai') {
        onNewAiMessage(transformedMessage);
      }
    });

    // Listen for new messages (plural - mobile format)
    socket.on('new_messages', (data: { game_messages: any[] }) => {
      const newMessages = data.game_messages.filter((msg: any) => {
        return !messageIdsRef.current.has(msg.id || String(msg.timestamp));
      });

      if (newMessages.length > 0) {
        newMessages.forEach((msg: any) => {
          const msgId = msg.id || String(msg.timestamp);
          messageIdsRef.current.add(msgId);
        });
        setMessages(prev => [...prev, ...newMessages]);

        if (onNewAiMessage) {
          const aiMessages = newMessages.filter((msg: any) => msg.type === 'ai');
          if (aiMessages.length > 0) {
            onNewAiMessage(aiMessages[aiMessages.length - 1]);
          }
        }
      }
    });

    socket.on('player_turn_start', (data: { current_player_options: string[] }) => {
      console.log('Player turn started, options:', data.current_player_options);
      setAiThinking(false);
      setGameState(prev => {
        if (!prev) return prev;
        return {
          ...prev,
          player_options: data.current_player_options
        };
      });
    });

    socket.on('winner_announcement', (data: any) => {
      console.log('Winner announcement received:', data);
      setWinnerInfo(data);
    });

    socket.on('player_eliminated', (data: EliminationEvent) => {
      console.log('Player eliminated:', data);
      setEliminationEvents(prev => [...prev, data]);
    });

    socket.on('tournament_complete', (data: TournamentResult) => {
      console.log('Tournament complete:', data);
      setTournamentResult(data);
    });

    // Listen for avatar updates (when background generation completes)
    socket.on('avatar_update', (data: { player_name: string; avatar_url: string; avatar_emotion: string }) => {
      console.log('Avatar update received:', data);
      setGameState(prev => {
        if (!prev) return prev;
        return {
          ...prev,
          players: prev.players.map(player =>
            player.name === data.player_name
              ? { ...player, avatar_url: data.avatar_url, avatar_emotion: data.avatar_emotion }
              : player
          )
        };
      });
    });
  }, [onNewAiMessage]);

  // refreshGameState: silent=true means don't touch loading state (for reconnections)
  const refreshGameState = useCallback(async (gId: string, silent = false): Promise<boolean> => {
    try {
      const res = await fetchWithCredentials(`${config.API_URL}/api/game-state/${gId}`);
      const data = await res.json();

      if (data.error || !data.players || data.players.length === 0) {
        return false;
      }

      setGameState(data);
      if (!silent) {
        setLoading(false);
      }

      if (data.messages) {
        setMessages(data.messages);
        // Clear and repopulate to prevent unbounded growth
        messageIdsRef.current.clear();
        data.messages.forEach((msg: ChatMessage) => messageIdsRef.current.add(msg.id));
      }

      const currentPlayer = data.players[data.current_player_idx];
      if (!currentPlayer.is_human) {
        setAiThinking(true);
      }

      return true;
    } catch (err) {
      console.error('Failed to refresh game state:', err);
      return false;
    }
  }, []);

  const createSocket = useCallback((gId: string) => {
    const socket = io(config.SOCKET_URL, {
      reconnection: true,
      reconnectionAttempts: Infinity,
      reconnectionDelay: 1000,
      reconnectionDelayMax: 5000,
      timeout: 20000,
    });

    socketRef.current = socket;

    socket.on('connect', () => {
      const isReconnect = !isInitialConnectionRef.current;
      console.log(`Socket ${isReconnect ? 're' : ''}connected, joining game:`, gId);
      socket.emit('join_game', gId);
      // Use silent mode for reconnections to avoid loading flash
      refreshGameState(gId, isReconnect);
      isInitialConnectionRef.current = false;
    });

    setupSocketListeners(socket);

    return socket;
  }, [refreshGameState, setupSocketListeners]);

  // Game initialization effect
  useEffect(() => {
    if (providedGameId) {
      const loadGameId = providedGameId;
      setGameId(loadGameId);
      localStorage.setItem('activePokerGameId', loadGameId);

      createSocket(loadGameId);

      refreshGameState(loadGameId).then(success => {
        if (!success) {
          console.error('Failed to load game');
          localStorage.removeItem('activePokerGameId');
          localStorage.removeItem('pokerGameState');
          if (onGameCreated) {
            onGameCreated('');
          }
          if (onGameLoadFailed) {
            onGameLoadFailed();
          } else {
            window.location.reload();
          }
        }
      });
    } else {
      fetchWithCredentials(`${config.API_URL}/api/new-game`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          playerName: playerName || 'Player'
        }),
      })
        .then(async res => {
          if (!res.ok) {
            const errorData = await res.json();
            throw new Error(errorData.error || 'Failed to create game');
          }
          return res.json();
        })
        .then(data => {
          const newGameId = data.game_id;
          setGameId(newGameId);
          localStorage.setItem('activePokerGameId', newGameId);

          if (onGameCreated) {
            onGameCreated(newGameId);
          }

          createSocket(newGameId);
          return refreshGameState(newGameId);
        })
        .catch(err => {
          console.error('Failed to create/fetch game:', err);
          setError(err.message || 'Failed to create game');
          setLoading(false);
        });
    }
  }, [providedGameId, createSocket, refreshGameState, playerName, onGameCreated, onGameLoadFailed]);

  // Handle visibility changes (browser wake from sleep)
  useEffect(() => {
    const handleVisibilityChange = () => {
      if (document.visibilityState === 'visible' && gameId) {
        console.log('Page became visible, checking connection...');
        const socket = socketRef.current;

        if (!socket || !socket.connected) {
          console.log('Socket disconnected, reconnecting...');
          if (socket) {
            socket.connect();
          } else {
            createSocket(gameId);
          }
        } else {
          // Silent refresh - just update state in background, no loading flash
          console.log('Socket connected, silently refreshing game state...');
          refreshGameState(gameId, true);
        }
      }
    };

    document.addEventListener('visibilitychange', handleVisibilityChange);

    return () => {
      document.removeEventListener('visibilitychange', handleVisibilityChange);
    };
  }, [gameId, createSocket, refreshGameState]);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      if (socketRef.current) {
        console.log('Disconnecting WebSocket');
        socketRef.current.disconnect();
      }
    };
  }, []);

  const handlePlayerAction = useCallback(async (action: string, amount?: number) => {
    if (!gameId) return;

    setAiThinking(true);

    try {
      const response = await fetchWithCredentials(`${config.API_URL}/api/game/${gameId}/action`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          action,
          amount: amount || 0
        }),
      });

      if (response.ok) {
        console.log('Action sent successfully, waiting for WebSocket updates');
      } else {
        throw new Error('Action failed');
      }
    } catch (error) {
      console.error('Failed to send action:', error);
      setAiThinking(false);
    }
  }, [gameId]);

  const handleSendMessage = useCallback(async (message: string) => {
    if (!gameId) return;

    try {
      await fetchWithCredentials(`${config.API_URL}/api/game/${gameId}/message`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          message,
          sender: playerName || 'Player'
        }),
      });
    } catch (error) {
      console.error('Failed to send message:', error);
    }
  }, [gameId, playerName]);

  return {
    gameState,
    loading,
    error,
    gameId,
    messages,
    aiThinking,
    winnerInfo,
    tournamentResult,
    eliminationEvents,
    socketRef,
    handlePlayerAction,
    handleSendMessage,
    clearWinnerInfo,
    clearTournamentResult,
    refreshGameState,
  };
}
