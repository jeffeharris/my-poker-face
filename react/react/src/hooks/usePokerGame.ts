import { useEffect, useState, useRef, useCallback } from 'react';
import { io, Socket } from 'socket.io-client';
import type { ChatMessage, GameState, WinnerInfo, BackendChatMessage } from '../types';
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
  winnerInfo: WinnerInfo | null;
  revealedCards: RevealedCardsInfo | null;
  tournamentResult: TournamentResult | null;
  eliminationEvents: EliminationEvent[];
  socketRef: React.MutableRefObject<Socket | null>;
  isConnected: boolean;
  showActionButtons: boolean;
  queuedAction: QueuedAction;
  setQueuedAction: (action: QueuedAction) => void;
  handlePlayerAction: (action: string, amount?: number) => Promise<void>;
  handleSendMessage: (message: string) => Promise<void>;
  clearWinnerInfo: () => void;
  clearTournamentResult: () => void;
  clearRevealedCards: () => void;
  refreshGameState: (gId: string, silent?: boolean) => Promise<boolean>;
  guestLimitReached: boolean;
  // Debug functions
  debugTriggerSplitPot: () => void;
  debugTriggerSidePot: () => void;
}

// Cap message arrays to prevent unbounded memory growth in long games
const MAX_MESSAGES = 200;

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
  const [winnerInfo, setWinnerInfo] = useState<WinnerInfo | null>(null);
  const [revealedCards, setRevealedCards] = useState<RevealedCardsInfo | null>(null);
  const [tournamentResult, setTournamentResult] = useState<TournamentResult | null>(null);
  const [guestLimitReached, setGuestLimitReached] = useState(false);
  const [eliminationEvents, setEliminationEvents] = useState<EliminationEvent[]>([]);
  const [isConnected, setIsConnected] = useState(false);
  // Cache avatar URLs by player/emotion so background generation results aren't lost
  const avatarCacheRef = useRef<Record<string, Record<string, string>>>({});
  const isInitialConnectionRef = useRef(true); // Track if this is first connection vs reconnect
  const [queuedAction, setQueuedAction] = useState<QueuedAction>(null);
  const queuedActionRef = useRef<QueuedAction>(null);
  const handlePlayerActionRef = useRef<(action: string, amount?: number) => Promise<void>>(() => Promise.resolve());
  const aiThinkingTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const gameIdRef = useRef<string | null>(null);

  const clearWinnerInfo = useCallback(() => {
    setWinnerInfo(null);
    setRevealedCards(null);
  }, []);
  const clearTournamentResult = useCallback(() => setTournamentResult(null), []);
  const clearRevealedCards = useCallback(() => setRevealedCards(null), []);

  // Keep refs in sync with state for use in socket callbacks
  useEffect(() => {
    queuedActionRef.current = queuedAction;
  }, [queuedAction]);
  useEffect(() => {
    gameIdRef.current = gameId;
  }, [gameId]);

  const clearAiThinkingTimeout = useCallback(() => {
    if (aiThinkingTimeoutRef.current) {
      clearTimeout(aiThinkingTimeoutRef.current);
      aiThinkingTimeoutRef.current = null;
    }
  }, []);

  const setupSocketListeners = useCallback((socket: Socket) => {
    socket.on('disconnect', () => {
      setIsConnected(false);
      // Clear aiThinking so UI doesn't appear stuck while disconnected
      setAiThinking(false);
      clearAiThinkingTimeout();
    });

    socket.on('player_joined', (_data: { message: string }) => {
      // Placeholder for future player join handling
    });

    socket.on('update_game_state', (data: { game_state: GameState }) => {
      clearAiThinkingTimeout();
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
          setMessages(prev => [...prev, ...newMessages].slice(-MAX_MESSAGES));

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
    socket.on('new_message', (data: { message: BackendChatMessage }) => {
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
      setMessages(prev => [...prev, transformedMessage].slice(-MAX_MESSAGES));

      if (onNewAiMessage && transformedMessage.type === 'ai') {
        onNewAiMessage(transformedMessage);
      }
    });

    // Listen for new messages (plural - mobile format)
    socket.on('new_messages', (data: { game_messages: BackendChatMessage[] }) => {
      const newMessages = data.game_messages.filter((msg) => {
        return !messageIdsRef.current.has(msg.id || String(msg.timestamp));
      });

      if (newMessages.length > 0) {
        newMessages.forEach((msg) => {
          const msgId = msg.id || String(msg.timestamp);
          messageIdsRef.current.add(msgId);
        });
        setMessages(prev => [...prev, ...newMessages as unknown as ChatMessage[]].slice(-MAX_MESSAGES));

        if (onNewAiMessage) {
          const aiMessages = newMessages.filter((msg) => msg.message_type === 'ai');
          if (aiMessages.length > 0) {
            onNewAiMessage(aiMessages[aiMessages.length - 1] as unknown as ChatMessage);
          }
        }
      }
    });

    socket.on('player_turn_start', (data: { current_player_options: string[], cost_to_call: number }) => {
      clearAiThinkingTimeout();
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

    socket.on('winner_announcement', (data: WinnerInfo) => {
      clearAiThinkingTimeout();
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

    socket.on('guest_limit_reached', () => {
      setGuestLimitReached(true);
    });

    // Listen for avatar updates (when background generation completes)
    // Always cache the URL so it's available when needed later.
    // Only update the displayed avatar if:
    // - The player has no avatar yet (initial generation)
    // - The generated emotion matches what the player is currently showing
    socket.on('avatar_update', (data: { player_name: string; avatar_url: string; avatar_emotion: string }) => {
      logger.debug(`[RunOut Reaction] ${data.player_name} → ${data.avatar_emotion}`, data);
      // Always cache — prevents losing URLs when emotions change during generation
      if (!avatarCacheRef.current[data.player_name]) {
        avatarCacheRef.current[data.player_name] = {};
      }
      avatarCacheRef.current[data.player_name][data.avatar_emotion] = data.avatar_url;
      setGameState(prev => {
        if (!prev) return prev;
        return {
          ...prev,
          players: prev.players.map(player => {
            if (player.name !== data.player_name) return player;
            // Always apply if player has no avatar yet
            if (!player.avatar_url) {
              return { ...player, avatar_url: data.avatar_url, avatar_emotion: data.avatar_emotion };
            }
            // Apply if the generated emotion matches what the player is currently showing
            if (player.avatar_emotion === data.avatar_emotion) {
              return { ...player, avatar_url: data.avatar_url };
            }
            // Check cache: if the player's current emotion was previously generated, use it
            const cachedUrl = avatarCacheRef.current[player.name]?.[player.avatar_emotion || ''];
            if (cachedUrl && player.avatar_url !== cachedUrl) {
              return { ...player, avatar_url: cachedUrl };
            }
            return player;
          })
        };
      });
    });
  }, [onNewAiMessage, clearAiThinkingTimeout]);

  // refreshGameState: silent=true means don't touch loading state (for reconnections)
  const refreshGameState = useCallback(async (gId: string, silent = false): Promise<boolean> => {
    try {
      clearAiThinkingTimeout();
      const res = await fetchWithCredentials(`${config.API_URL}/api/game-state/${gId}`);
      if (!res.ok) {
        logger.error(`Failed to fetch game state: HTTP ${res.status}`);
        return false;
      }
      const data = await res.json();

      if (data.error || !data.players || data.players.length === 0) {
        return false;
      }

      const currentPlayer = data.players[data.current_player_idx];

      setGameState(data);
      if (!silent) {
        setLoading(false);
      }

      if (data.messages) {
        const capped = data.messages.slice(-MAX_MESSAGES);
        setMessages(capped);
        // Clear and repopulate to prevent unbounded growth
        messageIdsRef.current.clear();
        capped.forEach((msg: ChatMessage) => messageIdsRef.current.add(msg.id));
      }

      setAiThinking(!currentPlayer.is_human);

      return true;
    } catch (err) {
      logger.error('Failed to refresh game state:', err);
      return false;
    }
  }, [clearAiThinkingTimeout]);

  const createSocket = useCallback((gId: string) => {
    const socket = io(config.SOCKET_URL, {
      reconnection: true,
      reconnectionAttempts: Infinity,
      reconnectionDelay: 1000,
      reconnectionDelayMax: 5000,
      timeout: 20000,
      withCredentials: true,  // Send cookies for auth
    });

    socketRef.current = socket;

    socket.on('connect', async () => {
      const isReconnect = !isInitialConnectionRef.current;
      setIsConnected(true);
      socket.emit('join_game', gId);
      // Use silent mode for reconnections to avoid loading flash
      const success = await refreshGameState(gId, isReconnect);
      if (success && isReconnect && socket.connected) {
        // After server restart, the first join_game may have been rejected
        // because the game wasn't in memory yet. The REST call above reloads
        // it from persistence, so re-join to ensure we're in the Socket.IO room.
        socket.emit('join_game', gId);
      }
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
      if (aiThinkingTimeoutRef.current) {
        clearTimeout(aiThinkingTimeoutRef.current);
      }
    };
  }, []);

  const handlePlayerAction = useCallback(async (action: string, amount?: number) => {
    if (!gameId) return;

    // Clear any queued action since user is acting manually
    setQueuedAction(null);
    setAiThinking(true);

    // Safety net: if no socket event clears aiThinking within 30s, auto-refresh state
    clearAiThinkingTimeout();
    aiThinkingTimeoutRef.current = setTimeout(async () => {
      logger.warn('[RESILIENCE] aiThinking timeout — refreshing game state');
      const gId = gameIdRef.current;
      if (gId) {
        const success = await refreshGameState(gId, true);
        if (!success) {
          logger.warn('[RESILIENCE] refresh failed after timeout — clearing aiThinking');
          setAiThinking(false);
        }
      }
    }, 30000);

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

      if (!response.ok) {
        throw new Error('Action failed');
      }
    } catch (error) {
      logger.error('Failed to send action:', error);
      setAiThinking(false);
      clearAiThinkingTimeout();
    }
  }, [gameId, clearAiThinkingTimeout, refreshGameState]);

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

  const currentPlayer = gameState?.players[gameState.current_player_idx];
  const showActionButtons = !!(currentPlayer?.is_human &&
                             !currentPlayer.is_folded &&
                             gameState?.player_options &&
                             gameState.player_options.length > 0 &&
                             !aiThinking &&
                             isConnected);

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
    showActionButtons,
    queuedAction,
    setQueuedAction,
    handlePlayerAction,
    handleSendMessage,
    clearWinnerInfo,
    clearTournamentResult,
    clearRevealedCards,
    refreshGameState,
    guestLimitReached,
    // Debug functions
    debugTriggerSplitPot,
    debugTriggerSidePot,
  };
}
