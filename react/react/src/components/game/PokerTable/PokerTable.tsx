import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from 'react';
import { createPortal } from 'react-dom';
import { AnimatePresence } from 'framer-motion';
import toast from 'react-hot-toast';
import { Bot } from 'lucide-react';
import { Card, CommunityCard, HoleCard, DebugHoleCard } from '../../cards';
import { CharacterDetailCard } from '../../character';
import { dossierFromPlayer } from '../../character/dossierFromPlayer';
import { LLMDebugModal } from '../../mobile/LLMDebugModal';
import { CoachButton } from '../../mobile/CoachButton';
import { CoachBubble } from '../../mobile/CoachBubble';
import { SeatSpeechBubble } from '../SeatSpeechBubble/SeatSpeechBubble';
import { CoachDock } from '../CoachDock';
import { HeadsUpOpponentPanel } from '../../mobile/HeadsUpOpponentPanel';
import { PlayerThinking } from '../PlayerThinking';
import { WinnerAnnouncement } from '../WinnerAnnouncement';
import { TournamentComplete } from '../TournamentComplete';
import { StadiumLayout } from '../StadiumLayout';
import { GameHeader } from '../GameHeader';
import { PlayerCommandCenter } from '../PlayerCommandCenter';
import { StatsPanel } from '../StatsPanel';
import { CashControls } from '../../cash/CashControls';
import { BustModal } from '../../cash/BustModal';
import { SoloTableModal } from '../../cash/SoloTableModal';
import { ActivityFeed } from '../ActivityFeed';
import { ActionBadge, GuestLimitModal } from '../../shared';
import { ShuffleLoading, type TickerLine } from '../../shared/ShuffleLoading';
import { selectInterhandTicker } from '../../cash/interhandTicker';
import { feedEventKey, renderEventIcon } from '../../cash/tickerEvents';
import { useUsageStats } from '../../../hooks/useUsageStats';
import { useInterhandDirector } from '../../../hooks/useInterhandDirector';
import { useRunoutDirector } from '../../../hooks/useRunoutDirector';
import { useGameStore } from '../../../stores/gameStore';
import { pickQuote } from '../WinnerAnnouncement/quote-flavor';
import { useGuestChatLimit } from '../../../hooks/useGuestChatLimit';
import { useCoach } from '../../../hooks/useCoach';
import { logger } from '../../../utils/logger';
import { gameAPI } from '../../../utils/api';
import { avatarUrlForEmotion } from '../../../utils/avatarUrl';
import { config } from '../../../config';
import { usePokerGame } from '../../../hooks/usePokerGame';
import { useCommunityCardAnimation } from '../../../hooks/useCommunityCardAnimation';
import { useDisplayNickname } from '../../../stores/nicknameOverridesStore';
import { isBettingPhase } from '../../../constants/gamePhases';
import type { Player } from '../../../types/player';
import type { ChatMessage } from '../../../types';
import '../../../styles/action-badges.css';
import './PokerTable.css';

const MAX_INTERHAND_TICKER = 3;

interface PokerTableProps {
  gameId?: string | null;
  playerName?: string;
  onGameCreated?: (gameId: string) => void;
  /** Parent's back handler. Falls back to `window.location.href = '/'`
   *  if omitted, matching the legacy desktop behavior. */
  onBack?: () => void;
  /** Fired when the backend reports the game is gone (HTTP 404). Page
   *  level decides where to redirect — cash sessions go to /cash,
   *  tournaments to /menu. */
  onGameLoadFailed?: () => void;
}

export function PokerTable({
  gameId: providedGameId,
  playerName,
  onGameCreated,
  onBack,
  onGameLoadFailed,
}: PokerTableProps) {
  // Track last known actions for fade-out animation
  const lastKnownActions = useRef<Map<string, string>>(new Map());
  // Incrementing this state forces a re-render after the ref is mutated on fade completion
  const [, setFadeKey] = useState(0);

  // Opponent/human display names respect user-set nickname overrides.
  const displayNickname = useDisplayNickname();

  // Usage stats power the guest hand-limit modal.
  const { stats: usageStats } = useUsageStats();

  // Character dossier — opens when an opponent avatar is clicked.
  const [dossierPlayer, setDossierPlayer] = useState<Player | null>(null);
  const [dossierOrigin, setDossierOrigin] = useState<{ x: number; y: number } | undefined>();
  // LLM debug modal — replaces the dossier on avatar click when AI debug is on.
  const [debugModalPlayer, setDebugModalPlayer] = useState<Player | null>(null);
  const closeDebugModal = useCallback(() => setDebugModalPlayer(null), []);

  // Floating AI chat bubble — pops the latest AI message over the felt.
  const [recentAiMessage, setRecentAiMessage] = useState<ChatMessage | null>(null);
  const handleNewAiMessage = useCallback((message: ChatMessage) => {
    setRecentAiMessage(message);
  }, []);
  const dismissRecentAiMessage = useCallback(() => setRecentAiMessage(null), []);

  const openDossierForPlayer = useCallback((player: Player, target: HTMLElement) => {
    // In AI-debug mode, an avatar tap surfaces the LLM call breakdown instead
    // of the character dossier (mirrors mobile).
    if (config.ENABLE_AI_DEBUG && player.llm_debug) {
      setDebugModalPlayer(player);
      return;
    }
    const rect = target.getBoundingClientRect();
    setDossierOrigin({ x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 });
    setDossierPlayer(player);
  }, []);
  const closeDossier = useCallback(() => setDossierPlayer(null), []);

  // Use the shared hook for all socket/state management
  const {
    gameState,
    loading,
    error,
    gameId,
    messages,
    aiThinking,
    winnerInfo,
    revealedCards,
    tournamentResult,
    socketRef: _socketRef,
    isConnected,
    showActionButtons,
    handlePlayerAction,
    handleSendMessage,
    clearWinnerInfo,
    clearTournamentResult,
    cashBustEvent,
    clearCashBustEvent,
    queuedAction,
    setQueuedAction,
    guestLimitReached,
  } = usePokerGame({
    gameId: providedGameId ?? null,
    playerName,
    onGameCreated,
    onGameLoadFailed,
    onNewAiMessage: handleNewAiMessage,
  });

  // Community-card deal-in animation timing (flop cascade, turn/river single).
  const communityCardAnimations = useCommunityCardAnimation(
    gameState?.newly_dealt_count,
    gameState?.community_cards?.length ?? 0
  );

  // Desktop chat is free-text only (no quick-chat panel), so it's gated by the
  // guest free-chat lock rather than the per-turn quick-chat limit.
  const { wrappedSendMessage, guestChatDisabled, guestFreeChatLocked, isGuest } = useGuestChatLimit(
    gameState?.awaiting_action,
    handleSendMessage
  );

  // Coach panel state
  const [showCoachPanel, setShowCoachPanel] = useState(false);
  const openCoachPanel = useCallback(() => setShowCoachPanel(true), []);
  const closeCoachPanel = useCallback(() => setShowCoachPanel(false), []);

  // Coach hook — mirrors mobile wiring.
  // showActionButtons is available at this point (from usePokerGame above) and
  // mirrors mobile's !!showActionButtons usage as the isPlayerTurn signal.
  const coach = useCoach({
    gameId: providedGameId ?? null,
    playerName: playerName || '',
    isPlayerTurn: !!showActionButtons,
  });

  const coachEnabled = !isGuest && coach.mode !== 'off';

  // Coach on/off toggle (desktop GameHeader). The default mode is 'off', so
  // without this the coach would be unreachable — it restores the player's
  // last active mode, mirroring mobile's menu toggle.
  const handleCoachToggle = useCallback(() => {
    try {
      if (coach.mode !== 'off') {
        localStorage.setItem('coach_mode_before_off', coach.mode);
        coach.setMode('off');
      } else {
        const previous = localStorage.getItem('coach_mode_before_off');
        coach.setMode(previous === 'proactive' || previous === 'reactive' ? previous : 'reactive');
      }
    } catch (err) {
      logger.warn('localStorage unavailable for coach mode toggle:', err);
      coach.setMode(coach.mode !== 'off' ? 'off' : 'reactive');
    }
    // coach.setMode is stable; coach.mode is the only value read. Depending on
    // the whole `coach` object (a fresh literal each render) would make this
    // callback unstable and defeat GameHeader's memoization on every tick.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [coach.mode, coach.setMode]);

  const recommendedAction = coach.mode === 'off' ? null : coach.coachAction;
  const raiseToAmount = coach.mode === 'off' ? null : coach.coachRaiseTo;

  // When a hand ends, request a post-hand review from the coach. Skip guests:
  // a stale localStorage coach_mode can briefly read non-'off' before the
  // server config resolves, and the coach endpoints 401 for guests.
  useEffect(() => {
    if (winnerInfo && !isGuest && coach.mode !== 'off') {
      coach.fetchHandReview();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [winnerInfo, coach.fetchHandReview]);

  // Clear unread review indicator when the coach panel opens.
  useEffect(() => {
    if (showCoachPanel && coach.hasUnreadReview) {
      coach.clearUnreadReview();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [showCoachPanel, coach.clearUnreadReview]);

  // Skill unlock toasts — batch, stagger, then dismiss.
  useEffect(() => {
    if (coach.skillUnlockQueue.length === 0) return;

    const batch = [...coach.skillUnlockQueue];
    batch.forEach((id) => coach.dismissSkillUnlock(id));

    const timers = batch.map((skillId, i) => {
      const skillName =
        coach.progression?.skill_states[skillId]?.name ?? skillId.replace(/_/g, ' ');
      return setTimeout(() => {
        toast(`New skill unlocked: ${skillName}`, {
          duration: 4000,
          style: {
            background: 'rgba(20, 22, 30, 0.95)',
            color: '#eee',
            border: '1px solid rgba(52, 211, 153, 0.3)',
            borderRadius: '12px',
            fontSize: '13px',
          },
        });
      }, i * 600);
    });

    return () => {
      timers.forEach(clearTimeout);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [coach.skillUnlockQueue]);

  // Pick a flavor quote for shuffle screens. Stable per hand so it doesn't
  // flicker on re-renders during a single shuffle.
  const handNumberForQuote = gameState?.hand_number;
  const shuffleQuote = useMemo(() => {
    const q = pickQuote('between_hands');
    return q ? { text: q.text, attribution: q.attribution } : undefined;
    // handNumberForQuote is an intentional recompute key (not read inside): it
    // re-picks the random quote each new hand while staying stable on re-renders.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [handNumberForQuote]);

  // Interhand director — client-owned `result → shuffle → next hand` beat, so
  // the between-hand transition no longer depends on the backend's variable
  // phase pacing. The winner overlay owns the "result" beat and calls
  // `handleResultComplete` (auto-dismiss or Continue), which hands off to the
  // shuffle beat. The two are never on screen at once.
  const worldEvents = useGameStore((state) => state.worldEvents);
  const handNumber = gameState?.hand_number ?? 0;
  const { isShuffling, beginShuffle } = useInterhandDirector({
    hasWinner: !!winnerInfo,
    handNumber,
  });

  const handleResultComplete = useCallback(() => {
    // No shuffle beat on a tournament's final hand — TournamentComplete takes
    // over once the winner is cleared, and there's no next hand to deal.
    if (!winnerInfo?.is_final_hand) {
      beginShuffle();
    }
    clearWinnerInfo();
  }, [winnerInfo, beginShuffle, clearWinnerInfo]);

  // Runout director: stable store selectors for the director inputs.
  // `heroFolded` is read from the store directly so it's available before the
  // post-gameState `humanPlayer` derivation below (hooks must be called
  // unconditionally, before any conditional return).
  const runoutSchedule = useGameStore((s) => s.runoutSchedule);
  const runItOut = useGameStore((s) => s.runItOut);
  const setRunoutDirectorActive = useGameStore((s) => s.setRunoutDirectorActive);
  const updatePlayers = useGameStore((s) => s.updatePlayers);
  const storeHeroFolded = useGameStore(
    (s) => s.players?.find((p) => p.is_human)?.is_folded ?? false
  );

  // Stable callback that patches avatar_emotion + avatar_url on a single player
  // in the Zustand store — driven by the runout director's per-card reaction beat.
  const applyRunoutReaction = useCallback(
    (playerName: string, emotion: string) => {
      updatePlayers((prev) => {
        if (!prev) return prev;
        return prev.map((p) =>
          p.name === playerName
            ? {
                ...p,
                avatar_emotion: emotion,
                avatar_url: avatarUrlForEmotion(p.avatar_url, emotion),
              }
            : p
        );
      });
    },
    [updatePlayers]
  );

  const { heroCommitted, heroRetreating } = useRunoutDirector({
    schedule: runoutSchedule,
    runItOut,
    revealed: !!revealedCards,
    heroFolded: storeHeroFolded,
    communityCardCount: gameState?.community_cards?.length ?? 0,
    handNumber,
    fastForward: gameState?.fast_forward ?? false,
    applyReaction: applyRunoutReaction,
    setActive: setRunoutDirectorActive,
  });

  // Cash/career mode: the interhand pause becomes a "meanwhile, elsewhere"
  // world ticker (top rare beats around the room since this hand started).
  // `undefined` in tournament mode, where the hand-number badge stays.
  const interhandTicker = useMemo<TickerLine[] | undefined>(() => {
    if (!gameState?.cash_mode) return undefined;
    const thisHand = worldEvents.filter((w) => w.hand === handNumber).map((w) => w.event);
    return selectInterhandTicker(thisHand, MAX_INTERHAND_TICKER).map((e) => ({
      key: feedEventKey(e),
      icon: renderEventIcon(e.type),
      message: e.message,
    }));
  }, [gameState?.cash_mode, worldEvents, handNumber]);

  // Handle tournament completion - clean up and return to menu
  const handleTournamentComplete = useCallback(async () => {
    if (gameId) {
      try {
        await fetch(`${config.API_URL}/api/end_game/${gameId}`, {
          method: 'POST',
          credentials: 'include',
        });
      } catch (err) {
        logger.error('Failed to end game:', err);
      }
    }
    clearTournamentResult();
    // Navigate back to menu by reloading
    window.location.href = '/';
  }, [gameId, clearTournamentResult]);

  // Calculate seat position based on offset from human player
  // Players are anchored to fixed positions - only dealer/blind buttons rotate
  // Seat offset 1 (acts after human) = left side, higher offsets move right
  const getStadiumSeatStyle = (
    seatOffset: number,
    totalPlayers: number,
    headsUpShowdownSlot?: number
  ) => {
    if (headsUpShowdownSlot !== undefined) {
      const left = headsUpShowdownSlot === 0 ? 25 : 75;
      return {
        position: 'absolute' as const,
        left: `${left}%`,
        top: '24%',
        transform: 'translate(-50%, -50%) scale(1)',
      };
    }

    // Total opponents is totalPlayers - 1 (excluding human)
    const totalOpponents = totalPlayers - 1;

    // Map seat offset (1 to totalOpponents) to position index (0 to totalOpponents-1)
    // seatOffset 1 = leftmost (index 0), seatOffset N-1 = rightmost (index N-2)
    const positionIndex = seatOffset - 1;

    // Dynamic arc spread - tighter when fewer opponents to keep them closer together
    // Full arc (120°) for 5+ opponents, narrower for fewer
    const maxArcSpread = 120;
    const minArcSpread = 60; // For 2 opponents
    const arcSpread = totalOpponents <= 2 ? minArcSpread : totalOpponents <= 4 ? 80 : maxArcSpread;

    // Center the arc around 90° (top center)
    const centerAngle = 90;
    const startAngle = centerAngle + arcSpread / 2; // left side
    const endAngle = centerAngle - arcSpread / 2; // right side
    const angleRange = startAngle - endAngle;
    const angleStep = totalOpponents > 1 ? angleRange / (totalOpponents - 1) : 0;
    const angle = (startAngle - positionIndex * angleStep) * (Math.PI / 180);

    // Wider ellipse for stadium view - reduced radiusY to bring avatars down
    const radiusX = 42; // Horizontal radius as percentage
    const radiusY = 28; // Vertical radius as percentage (reduced to bring avatars down)

    // Calculate position on ellipse, with offset to clear the header
    const left = 50 + radiusX * Math.cos(angle);
    const top = 52 - radiusY * Math.sin(angle); // Start from 52% to position avatars

    // Dynamic scaling - larger cards when fewer opponents
    const scale = totalOpponents <= 2 ? 1.6 : totalOpponents <= 4 ? 1.3 : 1.0;

    return {
      position: 'absolute' as const,
      left: `${left}%`,
      top: `${top}%`,
      transform: `translate(-50%, -50%) scale(${scale})`,
    };
  };

  // Stadium view helpers
  const humanPlayer = gameState?.players.find((p: Player) => p.is_human);
  const humanPlayerIndex = gameState?.players.findIndex((p: Player) => p.is_human) ?? -1;
  // Memoize derived player arrays so useMemo deps are stable across re-renders.
  const opponents = useMemo(
    () => gameState?.players.filter((p: Player) => !p.is_human) ?? [],
    [gameState?.players]
  );
  const showdownOpponents = useMemo(
    () => opponents.filter((p: Player) => !p.is_folded),
    [opponents]
  );
  const foldedOpponents = useMemo(() => opponents.filter((p: Player) => p.is_folded), [opponents]);
  const isHeadsUpShowdownLayout =
    (gameState?.run_it_out || gameState?.phase === 'SHOWDOWN') && showdownOpponents.length === 2;

  // During showdown/run-out, when at least 2 opponents have revealed cards.
  const isInShowdown =
    !!revealedCards?.players_cards && Object.keys(revealedCards.players_cards).length >= 2;

  // Cascade reveal order: each opponent's cards slide in after the previous pair.
  // The CSS var --reveal-index drives per-opponent animation-delay.
  const revealOrder = useMemo(() => {
    const order = new Map<string, number>();
    const cards = revealedCards?.players_cards;
    if (!cards) return order;
    const rendered = isInShowdown ? showdownOpponents : opponents;
    let idx = 0;
    for (const p of rendered) {
      if (cards[p.name]) order.set(p.name, idx++);
    }
    return order;
  }, [revealedCards, isInShowdown, showdownOpponents, opponents]);
  const isHumanDealer = humanPlayerIndex === gameState?.current_dealer_idx;
  const isHumanSmallBlind = humanPlayerIndex === gameState?.small_blind_idx;
  const isHumanBigBlind = humanPlayerIndex === gameState?.big_blind_idx;

  // Don't highlight active player during run-it-out, non-betting phases, or when phase is not set
  const phase = gameState?.phase;
  const shouldHighlightActivePlayer = isBettingPhase(phase, gameState?.run_it_out);
  const isHumanCurrentPlayer =
    shouldHighlightActivePlayer && humanPlayerIndex === gameState?.current_player_idx;

  // The player currently to act — used to gate the preemptive check/fold
  // control so it only appears while we're waiting on a bot.
  const currentPlayer =
    gameState?.current_player_idx !== undefined
      ? gameState?.players[gameState.current_player_idx]
      : undefined;
  const currentPlayerIsAI = !!currentPlayer && !currentPlayer.is_human;

  if (error) {
    return (
      <div className="poker-table">
        <div className="initial-loading">
          <div style={{ fontSize: '48px', marginBottom: '20px' }}>⚠️</div>
          <h2>Unable to Start Game</h2>
          <p style={{ color: '#ff6b6b', marginBottom: '20px' }}>{error}</p>
          <button
            onClick={() => window.location.reload()}
            style={{
              padding: '12px 24px',
              fontSize: '16px',
              background: '#4a9eff',
              color: 'white',
              border: 'none',
              borderRadius: '8px',
              cursor: 'pointer',
            }}
          >
            Back to Menu
          </button>
        </div>
      </div>
    );
  }

  if (loading) {
    return (
      <div className="poker-table">
        <ShuffleLoading
          isVisible={true}
          message="Setting up the table"
          submessage="Shuffling cards and gathering players"
          quote={shuffleQuote}
        />
      </div>
    );
  }

  if (!gameState) return <div className="error">No game state available</div>;

  // Shared table content - community cards, pot, and overlays
  const renderTableCore = () => (
    <>
      {/* Community Cards Area */}
      <div className="community-area">
        <div className="pot-area">
          <div className="pot">
            <div className="pot-label">POT</div>
            <div className="pot-amount">${gameState.pot.total}</div>
          </div>
        </div>

        <div className="community-cards">
          {Array.from({ length: 5 }).map((_, i) => {
            const card = gameState.community_cards[i];
            const anim = communityCardAnimations[i];
            const isAnimating = !!card && anim?.shouldAnimate;
            if (!card) {
              return <CommunityCard key={`placeholder-${i}`} revealed={false} />;
            }
            return (
              <div
                key={i}
                className="community-card-anim"
                style={
                  isAnimating
                    ? {
                        animation: `communityCardDealIn ${anim.duration}s cubic-bezier(0.16, 1, 0.3, 1) ${anim.delay}s both`,
                      }
                    : undefined
                }
              >
                <CommunityCard card={card} revealed={true} />
              </div>
            );
          })}
        </div>
      </div>

      {/* Winner Announcement */}
      <WinnerAnnouncement
        winnerInfo={winnerInfo}
        onComplete={handleResultComplete}
        players={gameState.players}
        gameId={providedGameId}
        playerName={playerName}
        onSendMessage={wrappedSendMessage}
      />

      {/* Interhand shuffle beat — owns the screen between the winner overlay
          and the next hand dealing, holding a client minimum so a fast backend
          can't flash it. In cash mode it shows the world ticker. */}
      <ShuffleLoading
        isVisible={isShuffling}
        message="Shuffling"
        handNumber={gameState.cash_mode ? undefined : handNumber}
        ticker={interhandTicker}
        quote={shuffleQuote}
        variant="interhand"
      />

      {/* Tournament Complete */}
      {!winnerInfo?.is_final_hand && (
        <TournamentComplete result={tournamentResult} onComplete={handleTournamentComplete} />
      )}
    </>
  );

  // Stadium Layout - used for all desktop screen sizes
  // Show a reconnecting indicator when the socket drops but we still have
  // game state to render underneath (mirrors the mobile behavior).
  const showReconnecting = !isConnected && !!gameState;

  return (
    <>
      {showReconnecting && (
        <div className="desktop-reconnecting-overlay" data-testid="reconnecting-overlay">
          <div className="desktop-reconnecting-indicator">
            <div className="reconnecting-spinner" data-testid="reconnecting-spinner" />
            <span>Reconnecting...</span>
          </div>
        </div>
      )}
      <BustModal event={cashBustEvent} onDismiss={clearCashBustEvent} />
      <SoloTableModal cashMode={gameState.cash_mode} />
      {guestLimitReached &&
        usageStats &&
        createPortal(
          <GuestLimitModal
            handsPlayed={usageStats.hands_played}
            handsLimit={usageStats.hands_limit}
            onReturnToMenu={() => {
              if (onBack) onBack();
            }}
          />,
          document.body
        )}
      <StadiumLayout
        header={
          <GameHeader
            handNumber={gameState.hand_number}
            blinds={{ small: gameState.small_blind, big: gameState.big_blind }}
            phase={gameState.phase}
            location={
              gameState.cash_mode
                ? {
                    tableName: gameState.cash_mode.table_name,
                    stakeLabel: gameState.cash_mode.stake_label,
                  }
                : undefined
            }
            onBackClick={
              onBack ??
              (() => {
                window.location.href = '/';
              })
            }
            onCoachToggle={isGuest ? undefined : handleCoachToggle}
            coachActive={coachEnabled}
          />
        }
        leftPanel={
          humanPlayer && (
            <>
              {gameState.cash_mode && (
                <CashControls
                  cashMode={gameState.cash_mode}
                  playerStack={humanPlayer.stack}
                  handInProgress={
                    gameState.phase !== 'INITIALIZING_HAND' &&
                    gameState.phase !== 'HAND_OVER' &&
                    gameState.phase !== 'EVALUATING_HAND'
                  }
                />
              )}
              <StatsPanel
                humanPlayer={humanPlayer}
                players={gameState.players}
                potTotal={gameState.pot.total}
                handNumber={gameState.hand_number}
              />
              {/* Heads-up psychological read — desktop has the sidebar room to
                  keep this docked open (mobile tucks it into the opponent row). */}
              {opponents.length === 1 && gameId && (
                <HeadsUpOpponentPanel
                  opponent={opponents[0]}
                  gameId={gameId}
                  humanPlayerName={humanPlayer.name}
                />
              )}
            </>
          )
        }
        bottomCenter={
          humanPlayer && (
            <PlayerCommandCenter
              player={humanPlayer}
              isCurrentPlayer={isHumanCurrentPlayer}
              showActions={showActionButtons ?? false}
              playerOptions={gameState.player_options ?? []}
              highestBet={gameState.highest_bet}
              minRaise={gameState.min_raise}
              bigBlind={gameState.big_blind}
              potSize={gameState.pot.total}
              onAction={handlePlayerAction}
              isDealer={isHumanDealer}
              isSmallBlind={isHumanSmallBlind}
              isBigBlind={isHumanBigBlind}
              bettingContext={gameState.betting_context}
              fastForward={gameState.fast_forward ?? false}
              aiInstant={gameState.ai_instant ?? false}
              alwaysFastForward={gameState.always_fast_forward ?? false}
              aiThinking={aiThinking}
              currentPlayerIsAI={currentPlayerIsAI}
              queuedAction={queuedAction}
              onQueueCheckFold={() =>
                setQueuedAction(queuedAction === 'check_fold' ? null : 'check_fold')
              }
              onFastForward={
                gameId
                  ? (enabled: boolean) => {
                      gameAPI.fastForward(gameId, enabled).catch((e) => {
                        logger.warn('[FF] toggle failed', e);
                      });
                    }
                  : undefined
              }
              recommendedAction={recommendedAction}
              raiseToAmount={raiseToAmount}
              heroCommitted={heroCommitted}
              heroRetreating={heroRetreating}
            />
          )
        }
        rightPanel={
          <ActivityFeed
            messages={messages}
            onSendMessage={wrappedSendMessage}
            playerName={playerName}
            guestChatDisabled={guestChatDisabled}
            guestFreeChatLocked={guestFreeChatLocked}
            players={gameState?.players}
            gameId={providedGameId ?? undefined}
          />
        }
      >
        <div className="poker-table stadium-view">
          <div className="table-felt">
            {renderTableCore()}

            {/* Opponents in top arc - anchored by seat position relative to human */}
            <div className="players-area">
              {opponents.map((player) => {
                const playerIndex = gameState.players.findIndex((p) => p.name === player.name);
                const totalPlayers = gameState.players.length;

                // Calculate seat offset from human (1 = immediately after human clockwise)
                // This keeps players in fixed positions - only D/SB/BB buttons rotate
                const seatOffset = (playerIndex - humanPlayerIndex + totalPlayers) % totalPlayers;

                const isDealer = playerIndex === gameState.current_dealer_idx;
                const isSmallBlind = playerIndex === gameState.small_blind_idx;
                const isBigBlind = playerIndex === gameState.big_blind_idx;
                const isCurrentPlayer =
                  shouldHighlightActivePlayer && playerIndex === gameState.current_player_idx;

                // Compute avatar state: swap to "thinking" when AI is processing
                const isAiThinking = isCurrentPlayer && aiThinking && !player.is_human;
                const avatarUrl = isAiThinking
                  ? avatarUrlForEmotion(player.avatar_url, 'thinking')
                  : player.avatar_url;
                const avatarEmotion = isAiThinking ? 'thinking' : player.avatar_emotion || 'avatar';
                const headsUpShowdownSlot = isHeadsUpShowdownLayout
                  ? showdownOpponents.findIndex((p: Player) => p.name === player.name)
                  : -1;

                // This opponent is the most recent speaker — their chat bubble
                // pops up beneath this seat (lifted above neighbours via z-index).
                const isSpeaking = recentAiMessage?.sender === player.name;

                return (
                  <div
                    key={player.name}
                    className={`player-seat ${
                      isCurrentPlayer ? 'current-player' : ''
                    } ${player.is_folded ? 'folded' : ''} ${player.is_all_in ? 'all-in' : ''} ${
                      isCurrentPlayer && aiThinking ? 'thinking' : ''
                    }${isSpeaking ? ' is-speaking' : ''}`}
                    style={getStadiumSeatStyle(
                      seatOffset,
                      totalPlayers,
                      headsUpShowdownSlot >= 0 ? headsUpShowdownSlot : undefined
                    )}
                  >
                    <div className="position-indicators">
                      {isDealer && (
                        <div className="position-chip dealer-button" title="Dealer">
                          D
                        </div>
                      )}
                      {isSmallBlind && (
                        <div className="position-chip small-blind" title="Small Blind">
                          SB
                        </div>
                      )}
                      {isBigBlind && (
                        <div className="position-chip big-blind" title="Big Blind">
                          BB
                        </div>
                      )}
                    </div>

                    <div className="player-info">
                      <button
                        type="button"
                        className="player-avatar player-avatar--clickable"
                        onClick={(e) =>
                          openDossierForPlayer(player, e.currentTarget as HTMLElement)
                        }
                        aria-label={`Open dossier for ${player.name}`}
                      >
                        {avatarUrl ? (
                          <img
                            src={`${config.API_URL}${avatarUrl}`}
                            alt={`${player.name} - ${avatarEmotion}`}
                            className={`avatar-image${isAiThinking ? ' avatar-thinking' : ''}`}
                          />
                        ) : (
                          <span className="avatar-initial">
                            {player.name.charAt(0).toUpperCase()}
                          </span>
                        )}
                        {player.is_rule_bot && (
                          <span className="bot-badge" title="Rule-based training bot">
                            <Bot size={14} aria-hidden />
                          </span>
                        )}
                      </button>
                      <div className="player-details">
                        <div className="player-name">{displayNickname(player)}</div>
                        <div className="player-stack">${player.stack}</div>
                        {player.bet > 0 && <div className="player-bet">Bet: ${player.bet}</div>}
                        <ActionBadge
                          player={player}
                          lastKnownActions={lastKnownActions}
                          onFadeComplete={() => setFadeKey((k) => k + 1)}
                        />
                      </div>
                    </div>

                    {/* Hole cards: revealed face-up during showdown, hidden otherwise */}
                    {revealedCards?.players_cards[player.name] ? (
                      <div
                        className="player-revealed-cards"
                        style={
                          {
                            '--reveal-index': revealOrder.get(player.name) ?? 0,
                          } as CSSProperties
                        }
                      >
                        {revealedCards.players_cards[player.name].map((card, i) => (
                          <Card key={i} card={card} faceDown={false} size="small" />
                        ))}
                      </div>
                    ) : (
                      <div className="player-cards">
                        {config.ENABLE_AI_DEBUG ? (
                          <>
                            <DebugHoleCard debugInfo={player.llm_debug} />
                            <DebugHoleCard debugInfo={player.llm_debug} />
                          </>
                        ) : (
                          <>
                            <HoleCard visible={false} size="xsmall" />
                            <HoleCard visible={false} size="xsmall" />
                          </>
                        )}
                      </div>
                    )}

                    {isCurrentPlayer && aiThinking && (
                      <PlayerThinking playerName={player.name} position={seatOffset} />
                    )}

                    {/* Chat bubble pops up beneath the seat of the speaker. */}
                    <AnimatePresence>
                      {isSpeaking && recentAiMessage && (
                        <SeatSpeechBubble
                          message={recentAiMessage}
                          onDismiss={dismissRecentAiMessage}
                        />
                      )}
                    </AnimatePresence>
                  </div>
                );
              })}
            </div>

            {/* Ghost rail — folded opponents shrink to small circles in the
                top-left of the felt during showdown so active opponents get
                visual prominence. The arc seats of folded players are dimmed
                (via the `.folded` CSS) but kept in position so layout doesn't
                jump; the ghost rail is additive, not a replacement. */}
            {isInShowdown && foldedOpponents.length > 0 && (
              <div className="showdown-ghost-rail" data-testid="showdown-ghost-rail">
                {foldedOpponents.map((p) => (
                  <div key={p.name} className="showdown-ghost-avatar" title={displayNickname(p)}>
                    {p.avatar_url ? (
                      <img src={`${config.API_URL}${p.avatar_url}`} alt={displayNickname(p)} />
                    ) : (
                      <span className="showdown-ghost-initial">
                        {p.name.charAt(0).toUpperCase()}
                      </span>
                    )}
                  </div>
                ))}
              </div>
            )}

            {/* Bet chips hidden - bets shown in player seats */}
          </div>
        </div>
      </StadiumLayout>

      {/* Character dossier — opens when an opponent avatar is clicked.
       *  Uses the live Player blob for basics; the personality block
       *  isn't loaded here so trait/playstyle sections drop silently. */}
      <CharacterDetailCard
        isOpen={dossierPlayer !== null}
        onClose={closeDossier}
        character={dossierPlayer ? dossierFromPlayer(dossierPlayer) : { name: '' }}
        origin={dossierOrigin}
        identifier={dossierPlayer?.name}
        // Informant purchasing only in the Circuit (cash) — tournaments show
        // the scouted reads but no chip-cost buttons.
        circuitContext={!!gameState.cash_mode}
      />

      {/* LLM debug modal — opens from an opponent avatar when AI debug is on.
          Portaled to body per the repo overlay convention (a fixed-position
          overlay must not be trapped in a positioned/transformed ancestor). */}
      {debugModalPlayer !== null &&
        createPortal(
          <LLMDebugModal
            isOpen={true}
            onClose={closeDebugModal}
            playerName={debugModalPlayer?.name || ''}
            debugInfo={debugModalPlayer?.llm_debug}
          />,
          document.body
        )}

      {/* Coach Components — only shown for authenticated players with coach enabled */}
      {coachEnabled && (
        <>
          <CoachButton
            onClick={openCoachPanel}
            hasNewInsight={(!!coach.proactiveTip || coach.hasUnreadReview) && !showCoachPanel}
            isThinking={coach.isThinking}
          />

          <CoachBubble
            isVisible={
              !showCoachPanel &&
              coach.mode === 'proactive' &&
              !!showActionButtons &&
              !!coach.proactiveTip
            }
            tip={coach.proactiveTip}
            stats={coach.stats}
            onTap={openCoachPanel}
            onDismiss={coach.clearProactiveTip}
            coachingMode={coach.progression?.coaching_mode}
          />

          <CoachDock
            isOpen={showCoachPanel}
            onClose={closeCoachPanel}
            stats={coach.stats}
            messages={coach.messages}
            onSendQuestion={coach.sendQuestion}
            isThinking={coach.isThinking}
            mode={coach.mode}
            onModeChange={coach.setMode}
            progression={coach.progression}
            progressionFull={coach.progressionFull}
            onFetchProgression={coach.fetchProgression}
            onSkipAhead={coach.skipAhead}
          />
        </>
      )}
    </>
  );
}
