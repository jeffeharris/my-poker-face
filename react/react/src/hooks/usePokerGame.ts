import { useEffect, useState, useRef, useCallback } from 'react';
import { io, Socket } from 'socket.io-client';
import toast from 'react-hot-toast';
import type { ChatMessage, GameState, WinnerInfo, BackendChatMessage } from '../types';
import type { TournamentResult, EliminationEvent, BackendCard } from '../types/tournament';
import { config } from '../config';
import { logger } from '../utils/logger';
import { useGameStore, selectGameState } from '../stores/gameStore';
import { useShallow } from 'zustand/react/shallow';

interface UsePokerGameOptions {
  gameId: string | null;
  playerName?: string;
  onGameCreated?: (gameId: string) => void;
  onNewAiMessage?: (message: ChatMessage) => void;
  onGameLoadFailed?: () => void;
}

type QueuedAction = 'check_fold' | null;

// State buffer for card animation gating
enum BufferState {
  NORMAL = 'NORMAL',       // Apply updates immediately
  GATED = 'GATED',         // Cards animating — queue incoming updates
  REPLAYING = 'REPLAYING'  // Animation done — replay queued updates with delays
}

interface QueuedStateUpdate {
  gameState: GameState;
  timestamp: number;
  handNumber: number;  // For staleness detection - ignore updates from previous hands
}

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

// Delay between replaying queued state updates (allows UI to show each action separately)
const REPLAY_DELAY_MS = 1000;

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
  // Game state lives in Zustand store for granular subscriptions
  const applyGameState = useGameStore(state => state.applyGameState);
  const updateStorePlayers = useGameStore(state => state.updatePlayers);
  const updateStorePlayerOptions = useGameStore(state => state.updatePlayerOptions);
  const gameState = useGameStore(useShallow(selectGameState));

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

  // State buffer for card animation gating
  // Use refs (not useState) because socket callbacks capture values at registration time,
  // and we need to read the current buffer state when updates arrive
  const bufferStateRef = useRef<BufferState>(BufferState.NORMAL);
  const updateQueueRef = useRef<QueuedStateUpdate[]>([]);
  const replayTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const gateTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const prevCommunityCardCountRef = useRef<number>(0);

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

  // ========================================================================
  // State Buffer for Card Animation Gating
  // ========================================================================

  /**
   * Calculate gate duration based on newly dealt card count.
   * Matches frontend animation timing in useCommunityCardAnimation.ts
   */
  const calculateGateDuration = useCallback((newlyDealtCount: number): number => {
    if (newlyDealtCount === 3) {
      // Flop: 3 cards with 1s cascade delays (0s, 1s, 2s) + 0.825s animation each
      // Last card starts at 2s delay + 0.825s duration = 2.825s when last card lands
      return 2825;
    } else if (newlyDealtCount === 1) {
      // Turn/River: single card with 0.825s animation duration
      return 825;
    }
    return 0;
  }, []);

  /**
   * Clear gate and replay timeouts.
   */
  const clearBufferTimers = useCallback(() => {
    if (gateTimeoutRef.current) {
      clearTimeout(gateTimeoutRef.current);
      gateTimeoutRef.current = null;
    }
    if (replayTimeoutRef.current) {
      clearTimeout(replayTimeoutRef.current);
      replayTimeoutRef.current = null;
    }
  }, []);

  /**
   * Reset buffer to initial state (used on disconnect, refresh, etc.)
   */
  const resetBuffer = useCallback(() => {
    clearBufferTimers();
    updateQueueRef.current = [];
    bufferStateRef.current = BufferState.NORMAL;
    prevCommunityCardCountRef.current = 0;
  }, [clearBufferTimers]);

  /**
   * Handle messages from a game state update.
   */
  const handleMessagesFromState = useCallback((state: GameState) => {
    if (state.messages) {
      const newMessages = state.messages.filter((msg: ChatMessage) => {
        return !messageIdsRef.current.has(msg.id);
      });

      if (newMessages.length > 0) {
        newMessages.forEach((msg: ChatMessage) => messageIdsRef.current.add(msg.id));
        setMessages(prev => [...prev, ...newMessages].slice(-MAX_MESSAGES));

        if (onNewAiMessage) {
          const aiMessages = newMessages.filter((msg: ChatMessage) => msg.type === 'ai');
          if (aiMessages.length > 0) {
            onNewAiMessage(aiMessages[aiMessages.length - 1]);
          }
        }
      }
    }
  }, [onNewAiMessage]);

  /**
   * Update AI thinking state based on current player.
   */
  const updateAiThinkingFromState = useCallback((state: GameState) => {
    const players = state?.players;
    const idx = state?.current_player_idx;

    if (!players || idx === undefined || idx < 0 || idx >= players.length) {
      logger.error('[BUFFER] Invalid player state for AI thinking check', {
        playersLength: players?.length,
        currentPlayerIdx: idx
      });
      setAiThinking(false);
      return;
    }

    const currentPlayer = players[idx];
    setAiThinking(!currentPlayer.is_human && !currentPlayer.is_folded);
  }, []);

  /**
   * Apply a single state update (game state + messages + AI thinking).
   */
  const applyStateUpdate = useCallback((state: GameState) => {
    applyGameState(state);
    handleMessagesFromState(state);
    updateAiThinkingFromState(state);
  }, [applyGameState, handleMessagesFromState, updateAiThinkingFromState]);

  /**
   * Replay queued updates one by one with 1s delay between each.
   */
  const scheduleNextReplay = useCallback(() => {
    if (updateQueueRef.current.length === 0) {
      logger.debug('[BUFFER] Replay complete, returning to NORMAL');
      bufferStateRef.current = BufferState.NORMAL;
      replayTimeoutRef.current = null;
      return;
    }

    const nextUpdate = updateQueueRef.current.shift();
    if (!nextUpdate) {
      // Defensive check - should not happen given length check above
      logger.error('[BUFFER] Unexpected empty queue during replay');
      bufferStateRef.current = BufferState.NORMAL;
      replayTimeoutRef.current = null;
      return;
    }

    logger.debug('[BUFFER] Replaying update', {
      remaining: updateQueueRef.current.length,
      queuedAt: nextUpdate.timestamp
    });

    applyStateUpdate(nextUpdate.gameState);

    // Schedule next replay after delay
    replayTimeoutRef.current = setTimeout(() => {
      try {
        scheduleNextReplay();
      } catch (error) {
        logger.error('[BUFFER] Replay failed, resetting to NORMAL:', error);
        resetBuffer();
      }
    }, REPLAY_DELAY_MS);
  }, [applyStateUpdate, resetBuffer]);

  /**
   * Start replaying queued updates after gate expires.
   */
  const startReplay = useCallback(() => {
    gateTimeoutRef.current = null;

    if (updateQueueRef.current.length === 0) {
      logger.debug('[BUFFER] No queued updates, returning to NORMAL');
      bufferStateRef.current = BufferState.NORMAL;
      return;
    }

    logger.debug('[BUFFER] Starting replay', {
      queueLength: updateQueueRef.current.length
    });
    bufferStateRef.current = BufferState.REPLAYING;
    scheduleNextReplay();
  }, [scheduleNextReplay]);

  /**
   * Process an incoming game state update with buffering logic.
   * - Detects newly dealt cards and opens a gate
   * - Queues updates during gate/replay
   * - Applies updates immediately in NORMAL state
   * - Interrupts replay and clears queue if new cards arrive during REPLAYING state
   */
  const processStateUpdate = useCallback((data: { game_state: GameState }) => {
    try {
      // Validate incoming data
      if (!data?.game_state) {
        logger.error('[BUFFER] Received invalid state update - missing game_state', { data });
        return;
      }

      const transformedState: GameState = {
        ...data.game_state,
        messages: data.game_state.messages || []
      };

      // Detect newly dealt community cards
      const currentCardCount = transformedState.community_cards?.length ?? 0;
      const newlyDealtCount = transformedState.newly_dealt_count ?? 0;
      const cardsJustDealt = newlyDealtCount > 0 && currentCardCount > prevCommunityCardCountRef.current;

      // Update tracking ref
      prevCommunityCardCountRef.current = currentCardCount;

      if (cardsJustDealt) {
        // Cards were just dealt - apply immediately (so animation starts) and open gate
        logger.debug('[BUFFER] Cards dealt, opening gate', { newlyDealtCount, currentCardCount });

        // If replaying when new cards arrive, interrupt replay and clear stale queue
        if (bufferStateRef.current === BufferState.REPLAYING) {
          clearBufferTimers();
          updateQueueRef.current = [];  // Clear stale updates from interrupted replay
        }

        // Apply card-dealing state immediately
        applyStateUpdate(transformedState);

        // Enter GATED state
        bufferStateRef.current = BufferState.GATED;

        // Set timer to start replay after animation completes
        const gateDuration = calculateGateDuration(newlyDealtCount);
        gateTimeoutRef.current = setTimeout(() => {
          try {
            startReplay();
          } catch (error) {
            logger.error('[BUFFER] startReplay failed, resetting to NORMAL:', error);
            resetBuffer();
          }
        }, gateDuration);

        return;
      }

      // Handle based on current buffer state
      if (bufferStateRef.current === BufferState.NORMAL) {
        // Normal mode: apply immediately
        applyStateUpdate(transformedState);
      } else {
        // GATED or REPLAYING: queue the update
        logger.debug(`[BUFFER] Queuing update during ${bufferStateRef.current}`, {
          queueLength: updateQueueRef.current.length + 1
        });
        updateQueueRef.current.push({
          gameState: transformedState,
          timestamp: Date.now(),
          handNumber: transformedState.hand_number ?? 0
        });
      }
    } catch (error) {
      logger.error('[BUFFER] Failed to process state update:', error);
      // Attempt recovery: reset buffer to prevent stuck state
      resetBuffer();
    }
  }, [applyStateUpdate, calculateGateDuration, clearBufferTimers, resetBuffer, startReplay]);

  // ========================================================================
  // Socket Listeners
  // ========================================================================

  const setupSocketListeners = useCallback((socket: Socket) => {
    socket.on('disconnect', () => {
      setIsConnected(false);
      // Clear aiThinking so UI doesn't appear stuck while disconnected
      setAiThinking(false);
      clearAiThinkingTimeout();
      // Reset state buffer on disconnect
      resetBuffer();
    });

    socket.on('player_joined', (_data: { message: string }) => {
      // Placeholder for future player join handling
    });

    socket.on('update_game_state', (data: { game_state: GameState }) => {
      clearAiThinkingTimeout();
      // Use buffer logic to handle card animation timing
      processStateUpdate(data);
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

      updateStorePlayerOptions(data.current_player_options);
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

    socket.on('rate_limited', (data: { event: string; message: string }) => {
      logger.warn(`Rate limited: ${data.event}`);
      toast.error(data.message);
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
      updateStorePlayers(prev => {
        if (!prev) return prev;
        return prev.map(player => {
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
        });
      });
    });
  }, [onNewAiMessage, clearAiThinkingTimeout, updateStorePlayers, updateStorePlayerOptions, resetBuffer, processStateUpdate]);

  // refreshGameState: silent=true means don't touch loading state (for reconnections)
  const refreshGameState = useCallback(async (gId: string, silent = false): Promise<boolean> => {
    try {
      clearAiThinkingTimeout();
      // Reset buffer on full refresh to prevent stale queued state
      resetBuffer();

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

      applyGameState(data);
      // Update card count ref to match refreshed state (prevents false gate trigger)
      prevCommunityCardCountRef.current = data.community_cards?.length ?? 0;

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
  }, [clearAiThinkingTimeout, applyGameState, resetBuffer]);

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
      // Clear buffer timers
      clearBufferTimers();
    };
  }, [clearBufferTimers]);

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
