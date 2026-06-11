import { useEffect, useState, useRef, useCallback } from 'react';
import { Socket } from 'socket.io-client';
import { createAuthedSocket } from '../utils/socket';
import toast from 'react-hot-toast';
import type {
  ChatMessage,
  GameState,
  WinnerInfo,
  BackendChatMessage,
  RevealedCardsInfo,
} from '../types';
import type { TournamentResult, EliminationEvent } from '../types/tournament';
import type { CashBustEvent, LobbyEvent } from '../components/cash/types';
import type { RunoutSchedule } from '../types/runout';
import { config } from '../config';
import { logger } from '../utils/logger';
import { HAPTICS } from '../utils/haptics';
import { onAppResume } from '../utils/nativeApp';
import { useGameStore, selectGameState } from '../stores/gameStore';
import { useShallow } from 'zustand/react/shallow';
import { useHandSequencer } from './useHandSequencer';

interface SkillEvaluationFeedback {
  skill_id: string;
  skill_name: string;
  verdict: 'correct' | 'incorrect' | 'marginal';
  reasoning: string;
  confidence: number;
}

// Training mode returns a per-action coach verdict in the action response.
// Surface it as a brief, calm toast (single id so rapid actions replace rather
// than stack). Other modes never include this field.
function showCoachFeedback(ev: SkillEvaluationFeedback): void {
  const mark = ev.verdict === 'correct' ? '✓' : ev.verdict === 'incorrect' ? '✗' : '•';
  const message = `${mark} ${ev.skill_name} — ${ev.reasoning}`;
  const opts = { id: 'coach-feedback', duration: 4500 } as const;
  if (ev.verdict === 'correct') {
    toast.success(message, opts);
  } else if (ev.verdict === 'incorrect') {
    toast.error(message, opts);
  } else {
    toast(message, { ...opts, icon: '🧐' });
  }
}

interface UsePokerGameOptions {
  gameId: string | null;
  playerName?: string;
  onGameCreated?: (gameId: string) => void;
  onNewAiMessage?: (message: ChatMessage) => void;
  onGameLoadFailed?: () => void;
  /** Fired when a scripted scene (Scene 0) completes — the backend signals the
   *  game should return to the lobby (where Sal's handoff beat continues). */
  onSceneComplete?: () => void;
}

export type QueuedAction = 'check_fold' | null;

interface UsePokerGameResult {
  gameState: GameState | null;
  loading: boolean;
  error: string | null;
  gameId: string | null;
  messages: ChatMessage[];
  aiThinking: boolean;
  winnerInfo: WinnerInfo | null;
  revealedCards: RevealedCardsInfo | null;
  /** Sequencer: true while the hand timeline is still playing back (actions /
   *  board / reactions draining). The "still going, not stalled" signal. */
  isPlaying: boolean;
  /** Sequencer: hero hole cards lifted to "present" at an all-in matchup. */
  heroCommitted: boolean;
  /** Sequencer: hero hole cards pulled back as the run-out board deals. */
  heroRetreating: boolean;
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
  // Cash mode bust state — populated by SocketIO `cash_bust` /
  // `cash_rebuy_needed` events from the backend. `null` means no
  // bust is currently active.
  cashBustEvent: (CashBustEvent & { kind: 'bust' | 'rebuy_needed' }) | null;
  clearCashBustEvent: () => void;
  // Debug functions
  debugTriggerSplitPot: () => void;
  debugTriggerSidePot: () => void;
}

// Cap message arrays to prevent unbounded memory growth in long games
const MAX_MESSAGES = 200;

// Backstop for a missed state broadcast: after the human acts we expect a
// game-state push (the next actor / board / winner) shortly. The first push
// CLEARS this timer (it never re-arms), so during a normal AI orbit it's gone
// within a second or two — it only ever fires when a push genuinely never
// arrived (dropped packet, room-join race after a reconnect), in which case we
// re-sync. Kept well above normal first-push latency, far below the old 30s
// stall that read as "my action didn't register".
const MISSED_PUSH_REFRESH_MS = 10000;

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
  onSceneComplete,
}: UsePokerGameOptions): UsePokerGameResult {
  // Game state lives in Zustand store for granular subscriptions
  const applyGameState = useGameStore((state) => state.applyGameState);
  const updateStorePlayers = useGameStore((state) => state.updatePlayers);
  const updateStorePlayerOptions = useGameStore((state) => state.updatePlayerOptions);
  const pushWorldEvent = useGameStore((state) => state.pushWorldEvent);
  const applyOptimisticAction = useGameStore((state) => state.applyOptimisticAction);
  const rollbackOptimisticAction = useGameStore((state) => state.rollbackOptimisticAction);
  const gameState = useGameStore(useShallow(selectGameState));

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [gameId, setGameId] = useState<string | null>(null);
  const [aiThinking, setAiThinking] = useState(false);
  const socketRef = useRef<Socket | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const messageIdsRef = useRef<Set<string>>(new Set());
  // AI message ids already seen at *enqueue* time — lets the sequencer flag the
  // beat that first carries each AI line (separate from messageIdsRef, which is
  // the apply-time display dedup).
  const enqueuedAiMsgIdsRef = useRef<Set<string>>(new Set());
  // Post-hand commentary timing. End-of-hand commentary is emitted ASAP by the
  // backend (async `new_message` pushes, seconds after the winner), which would
  // otherwise pop on screen WHILE the run-out / showdown is still animating. So
  // while the hand sequencer is mid-playback we buffer immediately-emitted chat
  // here instead of surfacing it, then flush the moment the timeline drains —
  // the comment lands right after the run-out, not on top of it. The backend is
  // unchanged; this only delays the client-side reveal. `isPlayingRef` mirrors
  // the sequencer's `isPlaying` for the (stable) socket handlers to read.
  const isPlayingRef = useRef(false);
  const pendingMessagesRef = useRef<ChatMessage[]>([]);
  const pendingAiMessagesRef = useRef<ChatMessage[]>([]);
  const [winnerInfo, setWinnerInfo] = useState<WinnerInfo | null>(null);
  const [revealedCards, setRevealedCards] = useState<RevealedCardsInfo | null>(null);
  const [tournamentResult, setTournamentResult] = useState<TournamentResult | null>(null);
  const [guestLimitReached, setGuestLimitReached] = useState(false);
  const [eliminationEvents, setEliminationEvents] = useState<EliminationEvent[]>([]);
  const [cashBustEvent, setCashBustEvent] = useState<
    (CashBustEvent & { kind: 'bust' | 'rebuy_needed' }) | null
  >(null);
  const clearCashBustEvent = useCallback(() => setCashBustEvent(null), []);
  const [isConnected, setIsConnected] = useState(false);
  // Cache avatar URLs by player/emotion so background generation results aren't lost
  const avatarCacheRef = useRef<Record<string, Record<string, string>>>({});
  const isInitialConnectionRef = useRef(true); // Track if this is first connection vs reconnect
  const [queuedAction, setQueuedAction] = useState<QueuedAction>(null);
  const queuedActionRef = useRef<QueuedAction>(null);
  const handlePlayerActionRef = useRef<(action: string, amount?: number) => Promise<void>>(() =>
    Promise.resolve()
  );
  const refreshGameStateRef = useRef<(gId: string, silent?: boolean) => Promise<boolean>>(() =>
    Promise.resolve(false)
  );
  const aiThinkingTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const gameIdRef = useRef<string | null>(null);
  const lastErrorRefreshRef = useRef<number>(0);
  // Latches when the backend has reported the game is gone (HTTP 404
  // on game-state). Cash sessions are in-memory-only, so a backend
  // restart drops the room and the frontend would otherwise reconnect-
  // spam socket.io with the stale sid forever. Once this is true we
  // disconnect the socket, fire onGameLoadFailed exactly once, and
  // skip subsequent refreshes.
  const gameGoneRef = useRef(false);

  // Stable refs for values the socket-lifecycle effect reads. The init effect
  // (below) must re-run ONLY when the game_id changes — never on a parent
  // re-render that hands down a fresh callback identity. Re-running it on an
  // unstable dep used to open a SECOND socket without tearing down the first,
  // leaving multiple live subscriptions to the same game_id all streaming into
  // the one shared store (the "two hands flickering" bug). Reading these
  // through refs keeps the effect's dep array down to [providedGameId].
  const createSocketRef = useRef<((gId: string) => Socket) | null>(null);
  const onGameCreatedRef = useRef<((gameId: string) => void) | undefined>(undefined);
  const onGameLoadFailedRef = useRef<(() => void) | undefined>(undefined);
  const playerNameRef = useRef<string | undefined>(playerName);
  onGameCreatedRef.current = onGameCreated;
  onGameLoadFailedRef.current = onGameLoadFailed;
  playerNameRef.current = playerName;

  const clearWinnerInfo = useCallback(() => {
    setWinnerInfo(null);
    setRevealedCards(null);
    // Belt-and-suspenders: also drop the run-out "stage" layout flag. Normally the
    // sequencer's scheduled setActive(false) clears it after the river hold, but on
    // iOS that timeout can be throttled/dropped while the WebView is busy, leaving
    // the table stuck in the expanded showdown layout with no overlay left to
    // dismiss. Clearing the winner is the definitive end of hand presentation, so
    // the stage must be down by here regardless of whether that timer fired.
    useGameStore.getState().setRunoutDirectorActive(false);
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
  // Hand-presentation sequencer (replaces the old card-animation state buffer)
  // ========================================================================

  /**
   * Handle messages from a game state update.
   */
  const handleMessagesFromState = useCallback(
    (state: GameState) => {
      if (state.messages) {
        const newMessages = state.messages.filter((msg: ChatMessage) => {
          return !messageIdsRef.current.has(msg.id);
        });

        if (newMessages.length > 0) {
          newMessages.forEach((msg: ChatMessage) => messageIdsRef.current.add(msg.id));
          setMessages((prev) => [...prev, ...newMessages].slice(-MAX_MESSAGES));

          if (onNewAiMessage) {
            // Forward EVERY new AI message in order (not just the last of the
            // batch) so the consumer can queue rapid-fire lines — e.g. Sal's
            // multi-line graduation. The non-Sal floater slot still keeps "last
            // wins" because each call overwrites it.
            newMessages
              .filter((msg: ChatMessage) => msg.type === 'ai')
              .forEach((msg: ChatMessage) => onNewAiMessage(msg));
          }
        }
      }
    },
    [onNewAiMessage]
  );

  /**
   * Update AI thinking state based on current player.
   */
  const updateAiThinkingFromState = useCallback((state: GameState) => {
    const players = state?.players;
    const idx = state?.current_player_idx;

    if (!players || idx === undefined || idx < 0 || idx >= players.length) {
      logger.error('[BUFFER] Invalid player state for AI thinking check', {
        playersLength: players?.length,
        currentPlayerIdx: idx,
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
  const applyStateUpdate = useCallback(
    (state: GameState) => {
      applyGameState(state);
      handleMessagesFromState(state);
      updateAiThinkingFromState(state);
    },
    [applyGameState, handleMessagesFromState, updateAiThinkingFromState]
  );

  // The sequencer owns the whole hand-playback timeline: it queues every backend
  // signal (actions, deals, the all-in reveal, the winner) and drains them beat-
  // by-beat on one clock, so the result is always the last beat (never outruns
  // the actions) and the run-out paces itself client-side. See
  // docs/plans/RUNOUT_PRESENTATION_SEQUENCER.md.
  const sequencer = useHandSequencer({
    applyState: applyStateUpdate,
    setReveal: setRevealedCards,
    setWinner: setWinnerInfo,
  });
  const {
    enqueueState,
    enqueueReveal,
    enqueueWinner,
    setSchedule,
    reset: resetSequencer,
    isPlaying,
    heroCommitted,
    heroRetreating,
  } = sequencer;

  // Release any chat buffered during hand playback (see pendingMessagesRef).
  const flushPendingMessages = useCallback(() => {
    const pending = pendingMessagesRef.current;
    if (pending.length > 0) {
      pendingMessagesRef.current = [];
      setMessages((prev) => [...prev, ...pending].slice(-MAX_MESSAGES));
    }
    const pendingAi = pendingAiMessagesRef.current;
    if (pendingAi.length > 0) {
      pendingAiMessagesRef.current = [];
      if (onNewAiMessage) pendingAi.forEach((msg) => onNewAiMessage(msg));
    }
  }, [onNewAiMessage]);

  // Surface a single immediately-emitted chat line — straight to the feed when
  // the table is idle, or buffered until the run-out drains when it isn't. The
  // singular path transforms to `type`; the plural (mobile) path pushes raw
  // backend rows keyed on `message_type`, so detect AI from either shape.
  const surfaceMessage = useCallback(
    (message: ChatMessage) => {
      const isAi =
        message.type === 'ai' || (message as { message_type?: string }).message_type === 'ai';
      if (isPlayingRef.current) {
        pendingMessagesRef.current.push(message);
        if (isAi) pendingAiMessagesRef.current.push(message);
        return;
      }
      setMessages((prev) => [...prev, message].slice(-MAX_MESSAGES));
      if (onNewAiMessage && isAi) onNewAiMessage(message);
    },
    [onNewAiMessage]
  );

  // Mirror `isPlaying` for the stable socket handlers, and flush buffered chat
  // the instant the sequencer's timeline drains (the run-out has finished).
  useEffect(() => {
    isPlayingRef.current = isPlaying;
    if (!isPlaying) flushPendingMessages();
  }, [isPlaying, flushPendingMessages]);

  // ========================================================================
  // Socket Listeners
  // ========================================================================

  const setupSocketListeners = useCallback(
    (socket: Socket) => {
      socket.on('disconnect', () => {
        setIsConnected(false);
        // Clear aiThinking so UI doesn't appear stuck while disconnected
        setAiThinking(false);
        clearAiThinkingTimeout();
        // Drop any in-flight sequencer timeline on disconnect
        resetSequencer();
      });

      socket.on('player_joined', (_data: { message: string }) => {
        // Placeholder for future player join handling
      });

      // A scripted scene (Scene 0) finished — hand off to the component, which
      // returns to the lobby once the mentor's closing lines have played.
      socket.on('scene_complete', () => {
        if (onSceneComplete) onSceneComplete();
      });

      socket.on('update_game_state', (data: { game_state: GameState }) => {
        if (!data?.game_state) {
          logger.error('[SEQUENCER] Received invalid state update — missing game_state', { data });
          return;
        }
        clearAiThinkingTimeout();
        const state = { ...data.game_state, messages: data.game_state.messages || [] };
        // Does this push carry new AI table talk? (messages are cumulative, so
        // track ids seen at enqueue time — independent of the apply-time dedup —
        // to flag the beat that first carries each AI line.) The sequencer floors
        // that beat in watchable mode so a sped-up action doesn't cut off the quip.
        let commentary = false;
        for (const msg of state.messages) {
          if (msg.type === 'ai' && !enqueuedAiMsgIdsRef.current.has(msg.id)) {
            enqueuedAiMsgIdsRef.current.add(msg.id);
            commentary = true;
          }
        }
        enqueueState(state, commentary);
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
          action: msg.action, // Include action for AI messages
          // Fully-qualify the speaker's avatar so the floating bubble can render
          // their face even after they've left the table (the seat-derived avatar
          // cache drops departed players). Backend sends a relative /api path.
          avatar_url: msg.avatar_url ? `${config.API_URL}${msg.avatar_url}` : undefined,
        };

        messageIdsRef.current.add(msgId);
        // Buffered while the run-out animates, surfaced immediately otherwise.
        surfaceMessage(transformedMessage);
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
            // Buffered while the run-out animates, surfaced immediately otherwise.
            // Per-message preserves order so Sal's back-to-back lines all queue.
            surfaceMessage(msg as unknown as ChatMessage);
          });
        }
      });

      socket.on(
        'player_turn_start',
        (data: { current_player_options: string[]; cost_to_call: number }) => {
          clearAiThinkingTimeout();
          setAiThinking(false);

          // Check for queued preemptive action
          if (queuedActionRef.current === 'check_fold') {
            const action = data.cost_to_call === 0 ? 'check' : 'fold';
            // Clear ref IMMEDIATELY (synchronously) to prevent double-firing if another
            // player_turn_start arrives before React re-renders and updates the ref via useEffect
            queuedActionRef.current = null;
            setQueuedAction(null);
            handlePlayerActionRef.current(action);
            return; // Action will trigger new state update
          }

          // It's the human's turn to act — a rising two-tap "you're up" cue to
          // draw attention back to the table. Native-only no-op on web.
          HAPTICS.turn();

          updateStorePlayerOptions(data.current_player_options);
        }
      );

      socket.on('winner_announcement', (data: WinnerInfo) => {
        clearAiThinkingTimeout();
        // Enqueue the verdict as the terminal beat — it lands only after every
        // queued action/board beat has drained, so the result can't outrun the
        // actions that produced it.
        enqueueWinner(data);
        // Clear queue when hand ends (sync ref immediately for consistency)
        queuedActionRef.current = null;
        setQueuedAction(null);
      });

      // Hole cards reveal at an all-in run-out showdown — a sequencer beat so the
      // matchup hold and the hero card-commit gesture are paced on the one clock.
      socket.on('reveal_hole_cards', (data: RevealedCardsInfo) => {
        enqueueReveal(data);
      });

      // Per-card run-out reaction schedule. Emitted once at the all-in hole-card
      // reveal; carries reactions + timing only (no board cards). Stored as data;
      // the sequencer resolves each card's faces from it at fire time.
      socket.on('runout_schedule', (data: RunoutSchedule) => {
        setSchedule(data);
      });

      socket.on('player_eliminated', (data: EliminationEvent) => {
        setEliminationEvents((prev) => [...prev, data]);
      });

      socket.on('tournament_complete', (data: TournamentResult) => {
        setTournamentResult(data);
      });

      socket.on('guest_limit_reached', () => {
        setGuestLimitReached(true);
      });

      socket.on('game_error', (data: { error: string; details?: string; recoverable: boolean }) => {
        logger.error(`Game error: ${data.error}`, data.details ?? '');

        if (data.recoverable) {
          toast.error(data.error);
          const now = Date.now();
          if (now - lastErrorRefreshRef.current > 5000) {
            lastErrorRefreshRef.current = now;
            const gId = gameIdRef.current;
            if (gId) refreshGameStateRef.current(gId, true);
          }
        } else {
          toast.error(`${data.error}. Please refresh the page.`, { duration: 10000 });
        }
      });

      socket.on('rate_limited', (data: { event: string; message: string }) => {
        logger.warn(`Rate limited: ${data.event}`);
        toast.error(data.message);
      });

      // PRH-31: the backend emits `reload_required` when a socket action hits a
      // game that was evicted from memory (server restart / TTL) — the action
      // path can't cold-load, only GET /api/game-state can. Self-heal by
      // re-fetching state, which rehydrates the game from persistence.
      socket.on('reload_required', (_data: { game_id?: string; code?: string }) => {
        logger.warn('[RESILIENCE] reload_required from server — refreshing game state');
        const gId = gameIdRef.current;
        if (gId) refreshGameStateRef.current(gId, true);
      });

      // Cash mode: server-driven bust detection. `cash_rebuy_needed`
      // fires when the human's stack hits 0 but bankroll can still
      // afford a rebuy at this table; `cash_bust` fires when bankroll
      // is too low (player must leave and find a sponsor at /cash).
      socket.on('cash_rebuy_needed', (data: CashBustEvent) => {
        setCashBustEvent({ ...data, kind: 'rebuy_needed' });
      });
      socket.on('cash_bust', (data: CashBustEvent) => {
        setCashBustEvent({ ...data, kind: 'bust' });
      });

      // Cash/career mode: the realtime world ticker broadcasts `world_event`
      // to the per-user lobby room, which this game socket is already joined
      // to (the connect handler joins it for the game page too). Buffer them
      // so the interhand shuffle screen can show a "meanwhile, elsewhere"
      // digest. Buffering is harmless in tournament games; display is gated
      // on cashMode in the table component.
      socket.on('world_event', (event: LobbyEvent) => {
        pushWorldEvent(event);
      });

      // Listen for avatar updates (when background generation completes)
      // Always cache the URL so it's available when needed later.
      // Only update the displayed avatar if:
      // - The player has no avatar yet (initial generation)
      // - The generated emotion matches what the player is currently showing
      socket.on(
        'avatar_update',
        (data: {
          player_name: string;
          avatar_url: string;
          avatar_emotion: string;
          is_reaction?: boolean;
        }) => {
          logger.debug(`[RunOut Reaction] ${data.player_name} → ${data.avatar_emotion}`, data);
          // While the mobile run-out director owns reactions, drop the backend's
          // street-level reaction emits — the director plays finer per-card faces
          // and a late street-level emit would clobber them. Generation arrivals
          // (no is_reaction flag) still pass through. Desktop never sets the flag,
          // so it keeps the backend reactions (it has no director).
          if (data.is_reaction && useGameStore.getState().runoutDirectorActive) {
            return;
          }
          // Always cache — prevents losing URLs when emotions change during generation
          if (!avatarCacheRef.current[data.player_name]) {
            avatarCacheRef.current[data.player_name] = {};
          }
          avatarCacheRef.current[data.player_name][data.avatar_emotion] = data.avatar_url;
          updateStorePlayers((prev) => {
            if (!prev) return prev;
            return prev.map((player) => {
              if (player.name !== data.player_name) return player;
              // Run-out reactions are authoritative: apply the emotion AND url
              // immediately so the face changes on its own beat. Without this the
              // emotion would only arrive on the next full game-state push (one
              // street later), which is what made reactions feel "off a beat" and
              // let the showdown face get cut off by the hand-over screen.
              // The branches below are for async avatar-image generation arriving
              // after the displayed emotion may have moved on — there we must NOT
              // clobber the current emotion.
              if (data.is_reaction) {
                return {
                  ...player,
                  avatar_url: data.avatar_url,
                  avatar_emotion: data.avatar_emotion,
                };
              }
              // Always apply if player has no avatar yet
              if (!player.avatar_url) {
                return {
                  ...player,
                  avatar_url: data.avatar_url,
                  avatar_emotion: data.avatar_emotion,
                };
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
        }
      );
    },
    [
      surfaceMessage,
      onSceneComplete,
      clearAiThinkingTimeout,
      updateStorePlayers,
      updateStorePlayerOptions,
      pushWorldEvent,
      resetSequencer,
      enqueueState,
      enqueueReveal,
      enqueueWinner,
      setSchedule,
    ]
  );

  // Fires exactly once when the backend confirms the game no longer
  // exists. Disconnects the socket so the reconnect loop stops, then
  // hands control back to the caller (page-level routing decides
  // where to redirect — cash menu vs main menu).
  const handleGameGone = useCallback(() => {
    if (gameGoneRef.current) return;
    gameGoneRef.current = true;
    if (socketRef.current) {
      socketRef.current.disconnect();
    }
    if (onGameLoadFailed) {
      onGameLoadFailed();
    }
  }, [onGameLoadFailed]);

  // refreshGameState: silent=true means don't touch loading state (for reconnections)
  const refreshGameState = useCallback(
    async (gId: string, silent = false): Promise<boolean> => {
      if (gameGoneRef.current) return false;
      try {
        clearAiThinkingTimeout();
        // Drop any in-flight sequencer timeline on a full refresh — the fetched
        // state is applied directly below, with no stale replay.
        resetSequencer();

        // Fetch with a bounded retry on 404. A cash session that's
        // only in the DB (server restarted mid-session) rehydrates on
        // the first GET via the cold-load path — but a transient miss
        // right at navigation time (session/auth not ready yet, a
        // cold-load race) can 404 before the row is back. Retrying a
        // couple times with short backoff removes the "two error toasts
        // then it loaded" flap on resume, without masking a genuinely
        // gone game (after the retries exhaust we still declare it gone).
        const MAX_404_RETRIES = 2;
        let res = await fetchWithCredentials(`${config.API_URL}/api/game-state/${gId}`);
        for (let attempt = 0; res.status === 404 && attempt < MAX_404_RETRIES; attempt++) {
          await new Promise((resolve) => setTimeout(resolve, 300 * (attempt + 1)));
          if (gameGoneRef.current) return false;
          res = await fetchWithCredentials(`${config.API_URL}/api/game-state/${gId}`);
        }
        if (res.status === 404) {
          // Game is gone from the backend (cash sessions don't survive
          // a backend restart if the row can't be loaded; tournament
          // games could be evicted from memory). Stop trying.
          logger.warn(`Game ${gId} not found after retries — backend has no record`);
          handleGameGone();
          return false;
        }
        if (!res.ok) {
          logger.error(`Failed to fetch game state: HTTP ${res.status}`);
          return false;
        }
        const data = await res.json();

        if (data.error || !data.players || data.players.length === 0) {
          logger.error('Game state refresh failed:', data.error ?? 'No players in response');
          return false;
        }

        const currentPlayer = data.players[data.current_player_idx];

        // Authoritative cold-load snapshot: reset the store's frame-version
        // baseline so post-restart/reconnect frames apply (and stale socket
        // frames older than this snapshot are dropped). See gameStore.
        applyGameState(data, true);

        if (!silent) {
          setLoading(false);
        }

        if (data.messages) {
          const capped = data.messages.slice(-MAX_MESSAGES);
          setMessages(capped);
          // Clear and repopulate to prevent unbounded growth. Drop any chat
          // buffered for a run-out that this authoritative snapshot supersedes —
          // the snapshot already contains those lines, so a later flush would
          // duplicate them (ids were just cleared, defeating the dedup).
          messageIdsRef.current.clear();
          enqueuedAiMsgIdsRef.current.clear();
          pendingMessagesRef.current = [];
          pendingAiMessagesRef.current = [];
          capped.forEach((msg: ChatMessage) => {
            messageIdsRef.current.add(msg.id);
            if (msg.type === 'ai') enqueuedAiMsgIdsRef.current.add(msg.id);
          });
        }

        setAiThinking(!currentPlayer.is_human);

        return true;
      } catch (err) {
        logger.error('Failed to refresh game state:', err);
        return false;
      }
    },
    [clearAiThinkingTimeout, applyGameState, resetSequencer, handleGameGone]
  );

  // Keep ref in sync for socket callback access
  refreshGameStateRef.current = refreshGameState;

  const createSocket = useCallback(
    (gId: string) => {
      // Pin to polling in dev — Werkzeug + Flask-SocketIO threading
      // mode can't reliably hold a WS upgrade and emits malformed
      // frames during the upgrade probe. Production (gunicorn +
      // GeventWebSocketWorker behind Caddy) handles WS fine, so let
      // socket.io negotiate normally there.
      const socket = createAuthedSocket(config.SOCKET_URL, {
        reconnection: true,
        reconnectionAttempts: Infinity,
        reconnectionDelay: 1000,
        reconnectionDelayMax: 5000,
        timeout: 20000,
        withCredentials: true, // Send cookies for auth
        // Own Manager per game socket. Without this, socket.io multiplexes on a
        // URL-keyed Manager and `disconnect()` leaves the instance in its nsp
        // cache, so the next createSocket (game switch / remount) hands back the
        // same socket and setupSocketListeners re-registers every handler on it —
        // duplicate listeners that fire N times and leak captured closures.
        forceNew: true,
        ...(import.meta.env.PROD ? {} : { transports: ['polling'] }),
      });

      socketRef.current = socket;

      socket.on('connect', async () => {
        const isReconnect = !isInitialConnectionRef.current;
        setIsConnected(true);
        socket.emit('join_game', gId);
        // Use silent mode for reconnections to avoid loading flash. This is the
        // single canonical state fetch for both initial connect and reconnects —
        // the init effect no longer double-fetches alongside it.
        const success = await refreshGameState(gId, isReconnect);
        if (success && isReconnect && socket.connected) {
          // After server restart, the first join_game may have been rejected
          // because the game wasn't in memory yet. The REST call above reloads
          // it from persistence, so re-join to ensure we're in the Socket.IO room.
          socket.emit('join_game', gId);
        } else if (!success && !isReconnect && !gameGoneRef.current) {
          // Initial load failed for a non-404 reason (a 404 already routed via
          // handleGameGone → onGameLoadFailed). Hand control to the page-level
          // callbacks, mirroring the previous explicit-fetch failure path.
          logger.error('Failed to load game');
          onGameCreatedRef.current?.('');
          onGameLoadFailedRef.current?.();
        }
        isInitialConnectionRef.current = false;
      });

      setupSocketListeners(socket);

      return socket;
    },
    [refreshGameState, setupSocketListeners]
  );
  // Keep ref in sync so the init effect can create the socket without taking
  // createSocket as a dependency (which would re-run it and leak sockets).
  createSocketRef.current = createSocket;

  // Game initialization effect.
  //
  // Keyed ONLY on providedGameId so it runs once per game: it opens exactly one
  // socket and tears it down on cleanup (game_id change or unmount). All other
  // values it touches are read through refs, so a parent re-render handing down
  // a fresh callback identity no longer re-runs this effect and leaks a second
  // socket. The socket's own `connect` handler owns the initial state fetch —
  // there is no separate refreshGameState here (that used to double-fetch and
  // race the connect-handler fetch into the shared store).
  useEffect(() => {
    // Belt-and-suspenders: never leave a prior socket connected when (re)running.
    if (socketRef.current) {
      socketRef.current.removeAllListeners();
      socketRef.current.disconnect();
      socketRef.current = null;
    }
    isInitialConnectionRef.current = true;

    if (providedGameId) {
      setGameId(providedGameId);
      createSocketRef.current?.(providedGameId);
    } else {
      fetchWithCredentials(`${config.API_URL}/api/new-game`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          playerName: playerNameRef.current || 'Player',
        }),
      })
        .then(async (res) => {
          if (!res.ok) {
            const errorData = await res.json();
            throw new Error(errorData.error || 'Failed to create game');
          }
          return res.json();
        })
        .then((data) => {
          const newGameId = data.game_id;
          setGameId(newGameId);

          onGameCreatedRef.current?.(newGameId);

          // The connect handler does the initial fetch for this socket too.
          createSocketRef.current?.(newGameId);
        })
        .catch((err) => {
          logger.error('Failed to create/fetch game:', err);
          setError(err.message || 'Failed to create game');
          setLoading(false);
        });
    }

    return () => {
      if (socketRef.current) {
        // removeAllListeners before disconnect so the ~25 handlers (and the
        // refs/setState they close over) are released immediately, not left to
        // GC the detached socket.
        socketRef.current.removeAllListeners();
        socketRef.current.disconnect();
        socketRef.current = null;
      }
      resetSequencer();
    };
  }, [providedGameId, resetSequencer]);

  // Recover from a backgrounded/suspended session (browser wake or native app
  // resume). iOS suspends the WebView while backgrounded: the socket dies and
  // pending setTimeouts (the sequencer pump) freeze, so on return we (1) drop any
  // frozen sequencer timeline before it can replay stale beats, (2) reconnect if
  // the socket dropped, and (3) re-sync to server truth. Reconnecting routes
  // through the socket `connect` handler, which itself calls refreshGameState.
  useEffect(() => {
    const recover = () => {
      if (!gameId || gameGoneRef.current) return;
      // F4: kill any frozen pump immediately — refreshGameState also resets the
      // sequencer, but a dropped socket takes the slower reconnect→fetch path.
      resetSequencer();
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
    };

    const handleVisibilityChange = () => {
      if (document.visibilityState === 'visible') recover();
    };

    document.addEventListener('visibilitychange', handleVisibilityChange);
    // Native: visibilitychange is unreliable in a WKWebView, so also drive
    // recovery off the Capacitor app-resume signal. No-op on web.
    const removeResume = onAppResume(recover);

    return () => {
      document.removeEventListener('visibilitychange', handleVisibilityChange);
      removeResume();
    };
  }, [gameId, createSocket, refreshGameState, resetSequencer]);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      if (socketRef.current) {
        socketRef.current.disconnect();
      }
      if (aiThinkingTimeoutRef.current) {
        clearTimeout(aiThinkingTimeoutRef.current);
      }
      // Drop any pending sequencer timers (the sequencer also self-cleans on unmount).
      resetSequencer();
    };
  }, [resetSequencer]);

  const handlePlayerAction = useCallback(
    async (action: string, amount?: number) => {
      if (!gameId) return;

      // Clear any queued action since user is acting manually
      // Sync ref immediately to prevent race conditions with socket events
      queuedActionRef.current = null;
      setQueuedAction(null);
      setAiThinking(true);

      // Optimistic UI: move the chips to the pot immediately so the tap feels
      // responsive instead of "submitting…". The authoritative game_state push
      // reconciles (and clears the rollback snapshot); we revert only on reject.
      applyOptimisticAction(action, amount);

      // Safety net: if no state push clears aiThinking shortly, auto-refresh.
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
      }, MISSED_PUSH_REFRESH_MS);

      const postAction = () =>
        fetchWithCredentials(`${config.API_URL}/api/game/${gameId}/action`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({
            action,
            amount: amount || 0,
          }),
        });

      try {
        let response = await postAction();

        // PRH-31: the action path can't cold-load — it returns 409
        // RELOAD_REQUIRED when the game was evicted from memory (restart / TTL).
        // Self-heal: re-fetch state (which rehydrates it via the GET cold-load
        // path) and retry the action once, so a tap right after a reconnect/
        // eviction isn't a silently-dropped dead button.
        if (response.status === 409) {
          let code: string | undefined;
          try {
            code = (await response.clone().json())?.code;
          } catch {
            code = undefined;
          }
          if (code === 'RELOAD_REQUIRED') {
            logger.warn('[RESILIENCE] action got RELOAD_REQUIRED — reloading state and retrying');
            await refreshGameState(gameId, true);
            response = await postAction();
          }
        }

        if (!response.ok) {
          throw new Error('Action failed');
        }

        // Training mode includes a per-action coach verdict — show it inline.
        try {
          const body = await response.json();
          if (body?.skill_evaluation) {
            showCoachFeedback(body.skill_evaluation as SkillEvaluationFeedback);
          }
        } catch {
          // Non-JSON / empty body (non-training games) — nothing to surface.
        }
      } catch (error) {
        logger.error('Failed to send action:', error);
        // The server didn't accept the action — undo the optimistic chip move
        // so the table doesn't show money in a pot it never reached.
        rollbackOptimisticAction();
        setAiThinking(false);
        clearAiThinkingTimeout();
      }
    },
    [
      gameId,
      clearAiThinkingTimeout,
      refreshGameState,
      applyOptimisticAction,
      rollbackOptimisticAction,
    ]
  );

  // Keep ref in sync for socket callback access (update synchronously)
  handlePlayerActionRef.current = handlePlayerAction;

  const handleSendMessage = useCallback(
    async (message: string, addressing?: string[], tone?: string, intensity?: string) => {
      if (!gameId) return;

      try {
        await fetchWithCredentials(`${config.API_URL}/api/game/${gameId}/message`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({
            message,
            sender: playerName || 'Player',
            ...(addressing && addressing.length > 0 ? { addressing } : {}),
            ...(tone ? { tone } : {}),
            ...(intensity ? { intensity } : {}),
          }),
        });
      } catch (error) {
        logger.error('Failed to send message:', error);
      }
    },
    [gameId, playerName]
  );

  // Debug function to trigger split pot scenario (two winners with equal hands)
  const debugTriggerSplitPot = useCallback(() => {
    const humanName = playerName || 'You';
    const mockWinnerData = {
      winners: [humanName, 'Batman'],
      pot_breakdown: [
        {
          pot_name: 'Main Pot',
          total_amount: 3000,
          winners: [
            { name: humanName, amount: 1500 },
            { name: 'Batman', amount: 1500 },
          ],
          hand_name: 'Two Pair',
        },
      ],
      hand_name: 'Two Pair',
      showdown: true,
      community_cards: [
        { rank: 'A', suit: 'hearts' },
        { rank: 'K', suit: 'hearts' },
        { rank: '7', suit: 'spades' },
        { rank: '7', suit: 'diamonds' },
        { rank: '2', suit: 'clubs' },
      ],
      players_showdown: {
        [humanName]: {
          cards: [
            { rank: 'A', suit: 'spades' },
            { rank: 'K', suit: 'diamonds' },
          ],
          hand_name: 'Two Pair',
          hand_rank: 7,
          kickers: ['2'],
        },
        Batman: {
          cards: [
            { rank: 'A', suit: 'clubs' },
            { rank: 'K', suit: 'spades' },
          ],
          hand_name: 'Two Pair',
          hand_rank: 7,
          kickers: ['2'],
        },
        Joker: {
          cards: [
            { rank: 'Q', suit: 'hearts' },
            { rank: 'J', suit: 'hearts' },
          ],
          hand_name: 'Pair',
          hand_rank: 8,
          kickers: ['A', 'K', 'Q'],
        },
      },
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
          hand_name: 'Flush',
        },
        {
          pot_name: 'Side Pot 1',
          total_amount: 2000,
          winners: [
            { name: humanName, amount: 1000 },
            { name: 'Batman', amount: 1000 },
          ],
          hand_name: 'Two Pair',
        },
        {
          pot_name: 'Side Pot 2',
          total_amount: 800,
          winners: [{ name: humanName, amount: 800 }],
          hand_name: 'Two Pair',
        },
      ],
      hand_name: 'Flush',
      showdown: true,
      community_cards: [
        { rank: 'A', suit: 'hearts' },
        { rank: '9', suit: 'hearts' },
        { rank: '7', suit: 'hearts' },
        { rank: '4', suit: 'spades' },
        { rank: '2', suit: 'clubs' },
      ],
      players_showdown: {
        [humanName]: {
          cards: [
            { rank: 'A', suit: 'spades' },
            { rank: 'A', suit: 'diamonds' },
          ],
          hand_name: 'Two Pair',
          hand_rank: 7,
          kickers: ['9'],
        },
        Batman: {
          cards: [
            { rank: 'A', suit: 'clubs' },
            { rank: '9', suit: 'spades' },
          ],
          hand_name: 'Two Pair',
          hand_rank: 7,
          kickers: ['7'],
        },
        Joker: {
          cards: [
            { rank: 'K', suit: 'hearts' },
            { rank: 'J', suit: 'hearts' },
          ],
          hand_name: 'Flush',
          hand_rank: 5,
          kickers: [],
        },
      },
    };
    setWinnerInfo(mockWinnerData);
  }, [playerName]);

  const currentPlayer = gameState?.players[gameState.current_player_idx];
  const showActionButtons = !!(
    currentPlayer?.is_human &&
    !currentPlayer.is_folded &&
    gameState?.player_options &&
    gameState.player_options.length > 0 &&
    !aiThinking &&
    isConnected
  );

  return {
    gameState,
    loading,
    error,
    gameId,
    messages,
    aiThinking,
    winnerInfo,
    revealedCards,
    isPlaying,
    heroCommitted,
    heroRetreating,
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
    cashBustEvent,
    clearCashBustEvent,
    // Debug functions
    debugTriggerSplitPot,
    debugTriggerSidePot,
  };
}
