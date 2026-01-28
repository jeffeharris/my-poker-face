import { useEffect, useState, useRef, useCallback } from 'react';
import { io, Socket } from 'socket.io-client';
import type { ChatMessage, GameState } from '../types';
import type { TournamentResult, EliminationEvent, BackendCard } from '../types/tournament';
import { config } from '../config';
import { logger } from '../utils/logger';

interface UsePokerGameOptions {
  gameId: string | null;
  playerName?: string;
  onGameCreated?: (gameId: string) => void;
  onNewAiMessage?: (message: ChatMessage) => void;
  onGameLoadFailed?: () => void;
}

type QueuedAction = 'check_fold' | null;

// Type for revealed hole cards during run-it-out showdown
interface RevealedCardsInfo {
  players_cards: Record<string, BackendCard[]>;
  community_cards: BackendCard[];
}

interface UsePokerGameResult {
  gameState: GameState | null;
  loading: boolean;
  error: string | null;
  gameId: string | null;
  messages: ChatMessage[];
  aiThinking: boolean;
  winnerInfo: any;
  revealedCards: RevealedCardsInfo | null;
  tournamentResult: TournamentResult | null;
  eliminationEvents: EliminationEvent[];
  socketRef: React.MutableRefObject<Socket | null>;
  isConnected: boolean;
  queuedAction: QueuedAction;
  setQueuedAction: (action: QueuedAction) => void;
  handlePlayerAction: (action: string, amount?: number) => Promise<void>;
  handleSendMessage: (message: string) => Promise<void>;
  clearWinnerInfo: () => void;
  clearTournamentResult: () => void;
  clearRevealedCards: () => void;
  refreshGameState: (gId: string) => Promise<boolean>;
  // Debug functions
  debugTriggerSplitPot: () => void;
  debugTriggerSidePot: () => void;
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
  const [revealedCards, setRevealedCards] = useState<RevealedCardsInfo | null>(null);
  const [tournamentResult, setTournamentResult] = useState<TournamentResult | null>(null);
  const [eliminationEvents, setEliminationEvents] = useState<EliminationEvent[]>([]);
  const [isConnected, setIsConnected] = useState(false);
  const isInitialConnectionRef = useRef(true); // Track if this is first connection vs reconnect
  const [queuedAction, setQueuedAction] = useState<QueuedAction>(null);
  const queuedActionRef = useRef<QueuedAction>(null);
  const handlePlayerActionRef = useRef<(action: string, amount?: number) => Promise<void>>(() => Promise.resolve());

  const clearWinnerInfo = useCallback(() => {
    setWinnerInfo(null);
    setRevealedCards(null);
  }, []);
  const clearTournamentResult = useCallback(() => setTournamentResult(null), []);
  const clearRevealedCards = useCallback(() => setRevealedCards(null), []);

  // Keep ref in sync with state for use in socket callbacks
  useEffect(() => {
    queuedActionRef.current = queuedAction;
  }, [queuedAction]);

  const setupSocketListeners = useCallback((socket: Socket) => {
    socket.on('disconnect', () => {
      setIsConnected(false);
    });

    socket.on('player_joined', (data: { message: string }) => {
    });

    socket.on('update_game_state', (data: { game_state: any }) => {
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

    socket.on('player_turn_start', (data: { current_player_options: string[], cost_to_call: number }) => {
      setAiThinking(false);

      // Check for queued preemptive action
      if (queuedActionRef.current === 'check_fold') {
        const action = data.cost_to_call === 0 ? 'check' : 'fold';
        setQueuedAction(null);
        handlePlayerActionRef.current(action);
        return; // Action will trigger new state update
      }

      setGameState(prev => {
        if (!prev) return prev;
        return {
          ...prev,
          player_options: data.current_player_options
        };
      });
    });

    socket.on('winner_announcement', (data: any) => {
      setWinnerInfo(data);
      setQueuedAction(null); // Clear queue when hand ends
    });

    // Listen for hole cards reveal during run-it-out showdown
    socket.on('reveal_hole_cards', (data: RevealedCardsInfo) => {
      setRevealedCards(data);
    });

    socket.on('player_eliminated', (data: EliminationEvent) => {
      setEliminationEvents(prev => [...prev, data]);
    });

    socket.on('tournament_complete', (data: TournamentResult) => {
      setTournamentResult(data);
    });

    // Listen for avatar updates (when background generation completes)
    socket.on('avatar_update', (data: { player_name: string; avatar_url: string; avatar_emotion: string }) => {
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
      logger.error('Failed to refresh game state:', err);
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
      setIsConnected(true);
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

      createSocket(loadGameId);

      refreshGameState(loadGameId).then(success => {
        if (!success) {
          logger.error('Failed to load game');
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

          if (onGameCreated) {
            onGameCreated(newGameId);
          }

          createSocket(newGameId);
          return refreshGameState(newGameId);
        })
        .catch(err => {
          logger.error('Failed to create/fetch game:', err);
          setError(err.message || 'Failed to create game');
          setLoading(false);
        });
    }
  }, [providedGameId, createSocket, refreshGameState, playerName, onGameCreated, onGameLoadFailed]);

  // Handle visibility changes (browser wake from sleep)
  useEffect(() => {
    const handleVisibilityChange = () => {
      if (document.visibilityState === 'visible' && gameId) {
        const socket = socketRef.current;

        if (!socket || !socket.connected) {
          if (socket) {
            socket.connect();
          } else {
            createSocket(gameId);
          }
        } else {
          // Silent refresh - just update state in background, no loading flash
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
        socketRef.current.disconnect();
      }
    };
  }, []);

  const handlePlayerAction = useCallback(async (action: string, amount?: number) => {
    if (!gameId) return;

    // Clear any queued action since user is acting manually
    setQueuedAction(null);
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
        // Action sent, WebSocket will deliver state updates
      } else {
        throw new Error('Action failed');
      }
    } catch (error) {
      logger.error('Failed to send action:', error);
      setAiThinking(false);
    }
  }, [gameId]);

  // Keep ref in sync for socket callback access (update synchronously)
  handlePlayerActionRef.current = handlePlayerAction;

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
      logger.error('Failed to send message:', error);
    }
  }, [gameId, playerName]);

  // Debug function to trigger split pot scenario (two winners with equal hands)
  const debugTriggerSplitPot = useCallback(() => {
    const humanName = playerName || 'You';
    const mockWinnerData = {
      winners: [humanName, 'Batman'],
      pot_breakdown: [{
        pot_name: 'Main Pot',
        total_amount: 3000,
        winners: [
          { name: humanName, amount: 1500 },
          { name: 'Batman', amount: 1500 }
        ],
        hand_name: 'Two Pair'
      }],
      hand_name: 'Two Pair',
      showdown: true,
      community_cards: [
        { rank: 'A', suit: 'hearts' },
        { rank: 'K', suit: 'hearts' },
        { rank: '7', suit: 'spades' },
        { rank: '7', suit: 'diamonds' },
        { rank: '2', suit: 'clubs' }
      ],
      players_showdown: {
        [humanName]: {
          cards: [{ rank: 'A', suit: 'spades' }, { rank: 'K', suit: 'diamonds' }],
          hand_name: 'Two Pair',
          hand_rank: 7,
          kickers: ['2']
        },
        'Batman': {
          cards: [{ rank: 'A', suit: 'clubs' }, { rank: 'K', suit: 'spades' }],
          hand_name: 'Two Pair',
          hand_rank: 7,
          kickers: ['2']
        },
        'Joker': {
          cards: [{ rank: 'Q', suit: 'hearts' }, { rank: 'J', suit: 'hearts' }],
          hand_name: 'Pair',
          hand_rank: 8,
          kickers: ['A', 'K', 'Q']
        }
      }
    };
    setWinnerInfo(mockWinnerData);
  }, [playerName]);

  // Debug function to trigger side pot scenario (all-in with multiple pots)
  const debugTriggerSidePot = useCallback(() => {
    const humanName = playerName || 'You';
    const mockWinnerData = {
      winners: [humanName, 'Batman'],
      pot_breakdown: [
        {
          pot_name: 'Main Pot',
          total_amount: 1500,
          winners: [{ name: 'Joker', amount: 1500 }],
          hand_name: 'Flush'
        },
        {
          pot_name: 'Side Pot 1',
          total_amount: 2000,
          winners: [
            { name: humanName, amount: 1000 },
            { name: 'Batman', amount: 1000 }
          ],
          hand_name: 'Two Pair'
        },
        {
          pot_name: 'Side Pot 2',
          total_amount: 800,
          winners: [{ name: humanName, amount: 800 }],
          hand_name: 'Two Pair'
        }
      ],
      hand_name: 'Flush',
      showdown: true,
      community_cards: [
        { rank: 'A', suit: 'hearts' },
        { rank: '9', suit: 'hearts' },
        { rank: '7', suit: 'hearts' },
        { rank: '4', suit: 'spades' },
        { rank: '2', suit: 'clubs' }
      ],
      players_showdown: {
        [humanName]: {
          cards: [{ rank: 'A', suit: 'spades' }, { rank: 'A', suit: 'diamonds' }],
          hand_name: 'Two Pair',
          hand_rank: 7,
          kickers: ['9']
        },
        'Batman': {
          cards: [{ rank: 'A', suit: 'clubs' }, { rank: '9', suit: 'spades' }],
          hand_name: 'Two Pair',
          hand_rank: 7,
          kickers: ['7']
        },
        'Joker': {
          cards: [{ rank: 'K', suit: 'hearts' }, { rank: 'J', suit: 'hearts' }],
          hand_name: 'Flush',
          hand_rank: 5,
          kickers: []
        }
      }
    };
    setWinnerInfo(mockWinnerData);
  }, [playerName]);

  return {
    gameState,
    loading,
    error,
    gameId,
    messages,
    aiThinking,
    winnerInfo,
    revealedCards,
    tournamentResult,
    eliminationEvents,
    socketRef,
    isConnected,
    queuedAction,
    setQueuedAction,
    handlePlayerAction,
    handleSendMessage,
    clearWinnerInfo,
    clearTournamentResult,
    clearRevealedCards,
    refreshGameState,
    // Debug functions
    debugTriggerSplitPot,
    debugTriggerSidePot,
  };
}
