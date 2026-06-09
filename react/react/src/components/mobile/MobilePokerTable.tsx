import { useEffect, useState, useRef, useCallback, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import { rememberAdminOrigin } from '../admin/adminOrigin';
import { useGuestChatLimit } from '../../hooks/useGuestChatLimit';
import type { ChatMessage } from '../../types';
import type { Player } from '../../types/player';
import { MobileOpponents } from './MobileOpponents';
import { MobileCommunityCards } from './MobileCommunityCards';
import { MobileHero } from './MobileHero';
import { MobileActionArea } from './MobileActionArea';
import { FloatingChat } from './FloatingChat';
import { SalFloater } from './SalFloater';
import { MobileWinnerAnnouncement } from './MobileWinnerAnnouncement';
import { TournamentComplete } from '../game/TournamentComplete';
import { MobileChatSheet } from './MobileChatSheet';
import { ShuffleLoading } from '../shared/ShuffleLoading';
import { GuestLimitModal } from '../shared';
import { useUsageStats } from '../../hooks/useUsageStats';
import { LLMDebugModal } from './LLMDebugModal';
import { CoachButton } from './CoachButton';
import { CoachPanel } from './CoachPanel';
import { CoachBubble } from './CoachBubble';
import { MobileCashSheet } from '../cash/MobileCashSheet';
import { BustModal } from '../cash/BustModal';
import { SoloTableModal } from '../cash/SoloTableModal';
import { leaveTable } from '../cash/api';
import { CharacterDetailCard } from '../character';
import { dossierFromPlayer } from '../character/dossierFromPlayer';
import { MenuBar, PotDisplay, GameInfoDisplay } from '../shared';
import { usePokerGame } from '../../hooks/usePokerGame';
import { useTournamentEvents } from '../../hooks/useTournamentEvents';
import { useGameStore } from '../../stores/gameStore';
import { useDisplayNickname } from '../../stores/nicknameOverridesStore';
import { useCardAnimation } from '../../hooks/useCardAnimation';
import { useCommunityCardAnimation } from '../../hooks/useCommunityCardAnimation';
import { useMobileCoach } from '../../hooks/useMobileCoach';
import { useInterhandMessaging } from '../../hooks/useInterhandMessaging';
import { useInterhandDirector } from '../../hooks/useInterhandDirector';
import { isBettingPhase } from '../../constants/gamePhases';
import { orderOpponentsRelativeToHuman } from '../../utils/playerOrdering';
import { logger } from '../../utils/logger';
import { config } from '../../config';
import '../../styles/action-badges.css';
import './MobilePokerTable.css';
import './MobileActionButtons.css';

interface MobilePokerTableProps {
  gameId?: string | null;
  playerName?: string;
  onGameCreated?: (gameId: string) => void;
  onBack?: () => void;
  onGameLoadFailed?: () => void;
}

export function MobilePokerTable({
  gameId: providedGameId,
  playerName,
  onGameCreated,
  onBack,
  onGameLoadFailed,
}: MobilePokerTableProps) {
  // Resolves opponent labels through the viewer's private nickname
  // overrides. Stable across renders so memoized children don't
  // bust when the function reference would otherwise churn.
  const displayNickname = useDisplayNickname();

  // Mobile-specific state
  const [showChatSheet, setShowChatSheet] = useState(false);
  const [showCashSheet, setShowCashSheet] = useState(false);
  const [recentAiMessage, setRecentAiMessage] = useState<ChatMessage | null>(null);
  // Sal's lines play through a QUEUE (see SalFloater) — he fires several in a row
  // (the graduation reveal is three) and a single "latest message" slot would
  // drop all but the last. Non-Sal AI chatter still uses the single slot above.
  const [salQueue, setSalQueue] = useState<ChatMessage[]>([]);
  const opponentsContainerRef = useRef<HTMLDivElement>(null);
  const opponentRefs = useRef<Map<string, HTMLDivElement>>(new Map());

  // Track last known actions for fade-out animation
  const lastKnownActions = useRef<Map<string, string>>(new Map());
  // Incrementing this state forces a re-render after the ref is mutated on fade completion
  const [, setFadeKey] = useState(0);

  // Callbacks for handling AI messages (for floating bubbles). Sal is special-
  // cased into a queue so every one of his lines surfaces; everyone else uses the
  // single "most recent" slot that FloatingChat renders.
  const handleNewAiMessage = useCallback((message: ChatMessage) => {
    if (message.sender === 'Sal Monroe') {
      setSalQueue((q) => (q.some((m) => m.id === message.id) ? q : [...q, message]));
    } else {
      setRecentAiMessage(message);
    }
  }, []);

  const dismissRecentAiMessage = useCallback(() => {
    setRecentAiMessage(null);
  }, []);

  const dismissSalMessage = useCallback((id: string) => {
    setSalQueue((q) => q.filter((m) => m.id !== id));
  }, []);

  // Scripted scene finished → return to the lobby once Sal's closing lines have
  // played (the SalFloater queue drains), where his handoff beat continues. The
  // backend fires `scene_complete`; we wait for the queue so the reveal isn't cut.
  const [pendingLobbyReturn, setPendingLobbyReturn] = useState(false);
  const handleSceneComplete = useCallback(() => setPendingLobbyReturn(true), []);

  // Coach state
  const [showCoachPanel, setShowCoachPanel] = useState(false);

  // LLM Debug modal state
  const [debugModalPlayer, setDebugModalPlayer] = useState<Player | null>(null);

  // Character dossier state — opens on opponent avatar tap.
  const [dossierPlayer, setDossierPlayer] = useState<Player | null>(null);
  const [dossierOrigin, setDossierOrigin] = useState<{ x: number; y: number } | undefined>();
  const closeDossier = useCallback(() => setDossierPlayer(null), []);
  const openDossierForPlayer = useCallback((player: Player, target: HTMLElement) => {
    const rect = target.getBoundingClientRect();
    setDossierOrigin({ x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 });
    setDossierPlayer(player);
  }, []);

  // Stable callbacks for child components to avoid re-renders
  const openChatSheet = useCallback(() => setShowChatSheet(true), []);
  const closeChatSheet = useCallback(() => setShowChatSheet(false), []);
  const openCashSheet = useCallback(() => setShowCashSheet(true), []);
  const closeCashSheet = useCallback(() => setShowCashSheet(false), []);
  const openCoachPanel = useCallback(() => setShowCoachPanel(true), []);
  const closeCoachPanel = useCallback(() => setShowCoachPanel(false), []);
  const closeDebugModal = useCallback(() => setDebugModalPlayer(null), []);
  const navigate = useNavigate();
  const navigateToAdmin = useCallback(() => {
    // Soft-navigate (not window.location.href) so the SPA history stack
    // survives — and record this game as the admin return origin, so the
    // admin back-arrow lands the player right back at this table.
    if (providedGameId) rememberAdminOrigin(`/game/${providedGameId}`);
    navigate('/admin');
  }, [navigate, providedGameId]);
  const handleFadeComplete = useCallback(() => setFadeKey((k) => k + 1), []);

  // Game state from Zustand store (granular selectors for fewer re-renders)
  const storePlayers = useGameStore((state) => state.players);
  const phase = useGameStore((state) => state.phase);
  const pot = useGameStore((state) => state.pot);
  const communityCards = useGameStore((state) => state.communityCards);
  const currentPlayerIdx = useGameStore((state) => state.currentPlayerIdx);
  const dealerIdx = useGameStore((state) => state.dealerIdx);
  const highestBet = useGameStore((state) => state.highestBet);
  const playerOptions = useGameStore((state) => state.playerOptions);
  const minRaise = useGameStore((state) => state.minRaise);
  const bigBlind = useGameStore((state) => state.bigBlind);
  const smallBlind = useGameStore((state) => state.smallBlind);
  const handNumber = useGameStore((state) => state.handNumber);
  const bettingContext = useGameStore((state) => state.bettingContext);
  const awaitingAction = useGameStore((state) => state.awaitingAction);
  const runItOut = useGameStore((state) => state.runItOut);
  const cashMode = useGameStore((state) => state.cashMode);
  const fastForward = useGameStore((state) => state.fastForward);
  const worldEvents = useGameStore((state) => state.worldEvents);
  const aiInstant = useGameStore((state) => state.aiInstant);
  const alwaysFastForward = useGameStore((state) => state.alwaysFastForward);

  // Non-game-state from the hook (socket, overlays, actions)
  const {
    loading,
    gameId,
    messages,
    aiThinking,
    winnerInfo,
    revealedCards,
    heroCommitted,
    heroRetreating,
    isPlaying,
    tournamentResult,
    socketRef,
    isConnected,
    showActionButtons,
    queuedAction,
    setQueuedAction,
    handlePlayerAction,
    handleSendMessage,
    clearWinnerInfo,
    clearTournamentResult,
    guestLimitReached,
    cashBustEvent,
    clearCashBustEvent,
  } = usePokerGame({
    gameId: providedGameId ?? null,
    playerName,
    onGameCreated,
    onNewAiMessage: handleNewAiMessage,
    onGameLoadFailed,
    onSceneComplete: handleSceneComplete,
  });

  // Multi-table tournament felt: relocation toasts + bust/win routing to the
  // standings hub (no-op for non-tournament games). See useTournamentEvents.
  useTournamentEvents({ socketRef, connected: isConnected, gameId });

  const { wrappedSendMessage, guestChatDisabled, guestFreeChatLocked, isGuest } = useGuestChatLimit(
    awaitingAction,
    handleSendMessage
  );

  // Usage stats for guest limit modal
  const { stats: usageStats } = useUsageStats();

  // Scene-complete → lobby return. Once Sal's closing lines have played out (the
  // floater queue is empty), cash out of the tutorial table and go to the lobby,
  // where his handoff beat greets the player. A fallback timer returns even if
  // the queue never drains so the player is never stranded.
  useEffect(() => {
    if (!pendingLobbyReturn) return undefined;
    const toLobby = () => {
      leaveTable()
        .catch(() => {})
        .finally(() => {
          window.location.href = '/cash';
        });
    };
    const delay = salQueue.length === 0 ? 1200 : 45000;
    const t = setTimeout(toLobby, delay);
    return () => clearTimeout(t);
  }, [pendingLobbyReturn, salQueue.length]);

  // Handle tournament completion - clean up and return to menu
  const handleTournamentComplete = useCallback(async () => {
    if (gameId) {
      try {
        const res = await fetch(`${config.API_URL}/api/end_game/${gameId}`, {
          method: 'POST',
          credentials: 'include',
        });
        if (!res.ok) {
          logger.error(`Failed to end game for gameId=${gameId}: HTTP ${res.status}`);
        }
      } catch (err) {
        logger.error(`Failed to end game for gameId=${gameId}:`, err);
      }
    }
    clearTournamentResult();
    // Call onBack if available, otherwise reload
    if (onBack) {
      onBack();
    } else {
      window.location.href = '/';
    }
  }, [gameId, clearTournamentResult, onBack]);

  const currentPlayer = storePlayers?.[currentPlayerIdx];
  const humanPlayer = storePlayers?.find((p) => p.is_human);
  const isShowdown = phase?.toLowerCase() === 'showdown';

  // Client-owned between-hand timeline. The winner overlay owns the "result"
  // beat (and calls handleResultComplete when its hold elapses or the player
  // taps Continue); the director owns the "shuffle" beat that follows. The two
  // are never on screen at once — shuffle starts only once the winner is
  // cleared. The run-out reactions, the hero card-commit gesture, and the
  // ordered verdict are all owned by the hand sequencer in usePokerGame now
  // (heroCommitted / heroRetreating come from there), so there's no separate
  // run-out director or winner-reveal gate to coordinate here.
  const { isShuffling, beginShuffle } = useInterhandDirector({
    hasWinner: !!winnerInfo,
    handNumber,
  });

  const handleResultComplete = useCallback(() => {
    // No shuffle beat on the tournament's final hand — there's no next hand to
    // deal, and TournamentComplete takes over once the winner is cleared.
    if (!winnerInfo?.is_final_hand) {
      beginShuffle();
    }
    clearWinnerInfo();
  }, [winnerInfo, beginShuffle, clearWinnerInfo]);

  // Between-hands ShuffleLoading content: the fold-out "walk" result line, a
  // per-hand flavor quote, and the cash-mode world ticker. The walk-result path
  // also drives the shuffle beat + clears the winner, so it takes those in.
  const { interhandMessage, interhandSubmessage, interhandQuote, interhandTicker } =
    useInterhandMessaging({
      winnerInfo,
      handNumber,
      cashMode,
      worldEvents,
      beginShuffle,
      clearWinnerInfo,
    });

  // Don't highlight active player during run-it-out, non-betting phases, or when phase is not set
  const shouldHighlightActivePlayer = isBettingPhase(phase, runItOut);

  // Card animation hook - handles dealing, exit animations, transforms
  const {
    displayCards,
    cardTransforms,
    isDealing,
    isExiting,
    cardsNeat,
    toggleCardsNeat,
    handleExitAnimationEnd,
  } = useCardAnimation({
    hand: humanPlayer?.hand,
  });

  // Community card animation hook - handles slide-in with cascade delays
  const communityCardAnimations = useCommunityCardAnimation(communityCards?.length ?? 0);

  // Auto-scroll to center the active opponent when turn changes
  useEffect(() => {
    if (!storePlayers || !currentPlayer || currentPlayer.is_human) return;

    const opponentEl = opponentRefs.current.get(currentPlayer.name);
    const containerEl = opponentsContainerRef.current;

    if (opponentEl && containerEl) {
      // Wait for CSS width transition to complete (300ms) before centering
      const timeoutId = setTimeout(() => {
        // Calculate scroll position to center the element with its new width
        const containerWidth = containerEl.offsetWidth;
        const elementLeft = opponentEl.offsetLeft;
        const elementWidth = opponentEl.offsetWidth;
        const scrollTarget = elementLeft - containerWidth / 2 + elementWidth / 2;

        containerEl.scrollTo({
          left: scrollTarget,
          behavior: 'smooth',
        });
      }, 320); // Slightly longer than 300ms transition

      return () => clearTimeout(timeoutId);
    }
    // Only re-run when current player changes, not on every gameState update
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentPlayerIdx, currentPlayer?.name]);

  // Sort opponents by their position relative to the human player in turn
  // order (shared with the quick-chat target picker so both line up).
  const opponents = useMemo(
    () => (storePlayers ? orderOpponentsRelativeToHuman(storePlayers) : []),
    [storePlayers]
  );

  // Separate active and folded opponents
  const activeOpponents = useMemo(() => opponents.filter((p) => !p.is_folded), [opponents]);
  const foldedOpponents = useMemo(() => opponents.filter((p) => p.is_folded), [opponents]);

  // During showdown, move folded players to the ghost rail so active players have more room in the main row
  const isInShowdown =
    revealedCards?.players_cards && Object.keys(revealedCards.players_cards).length >= 2;

  // Run-out reveal cascade order: each revealed opponent reveals after the
  // previous one finishes (and within an opponent, card 2 after card 1). The
  // index is the opponent's position among revealed opponents in render order;
  // the CSS turns it into a per-opponent animation-delay (var --reveal-index).
  const revealOrder = useMemo(() => {
    const order = new Map<string, number>();
    const cards = revealedCards?.players_cards;
    if (!cards) return order;
    const rendered = isInShowdown ? activeOpponents : opponents;
    let idx = 0;
    for (const p of rendered) {
      if (cards[p.name]) order.set(p.name, idx++);
    }
    return order;
  }, [revealedCards, isInShowdown, activeOpponents, opponents]);

  // Map of player name → avatar URL for FloatingChat. Accumulated across the
  // whole session (never pruned): a player who busts/leaves drops out of
  // `storePlayers`, but a chat message they sent right before going still
  // needs their avatar, so we remember every avatar we've ever seen here.
  const avatarCacheRef = useRef<Map<string, string>>(new Map());
  const playerAvatars = useMemo(() => {
    const cache = avatarCacheRef.current;
    for (const p of storePlayers ?? []) {
      if (p.avatar_url) cache.set(p.name, `${config.API_URL}${p.avatar_url}`);
    }
    // Fresh snapshot so memoized consumers see a new identity when it grows.
    return new Map(cache);
  }, [storePlayers]);

  // Heads-up mode: only 1 AI opponent remains
  const isHeadsUp = opponents.length === 1;
  const headsUpOpponent = isHeadsUp ? opponents[0] : null;

  // Two opponents mode: 2 AI opponents (3 players total)
  const isTwoOpponents = opponents.length === 2;
  const isThreeOpponents = opponents.length === 3;
  const isThreeOpponentsNormal = isThreeOpponents && !isInShowdown;
  const isThreeOpponentsShowdown = isInShowdown && activeOpponents.length === 3;

  // Coach integration (wraps useCoach + table glue: toggle, post-hand review,
  // unread-clear, skill-unlock toasts, recommendation values).
  const { coach, coachEnabled, recommendedAction, raiseToAmount, handleCoachToggle } =
    useMobileCoach({
      gameId: providedGameId ?? null,
      playerName: playerName || '',
      isPlayerTurn: !!showActionButtons,
      isGuest,
      winnerInfo,
      showCoachPanel,
    });

  const menuBarCenter = useMemo(
    () => (
      <GameInfoDisplay
        phase={phase}
        smallBlind={smallBlind}
        bigBlind={bigBlind}
        handNumber={handNumber}
        tableName={cashMode?.table_name}
      />
    ),
    [phase, smallBlind, bigBlind, handNumber, cashMode?.table_name]
  );

  const isInitialLoading = loading && !storePlayers;
  const hasGameData = Boolean(storePlayers && pot);

  // Only show error when not loading and still no data
  if (!isInitialLoading && !hasGameData) {
    return <div className="mobile-poker-table mobile-error">Failed to load game</div>;
  }

  // Show reconnecting indicator when disconnected but we still have game state
  const showReconnecting = !isConnected && storePlayers;

  return (
    <div
      className="mobile-poker-table"
      data-testid="mobile-poker-table"
      data-connected={isConnected ? 'true' : 'false'}
    >
      {/* Initial loading overlay - slides off screen when game data arrives */}
      <ShuffleLoading
        isVisible={isInitialLoading}
        message="Setting up the table"
        exitStyle="slide"
        quote={interhandQuote}
      />

      {/* Reconnecting overlay - shows when socket is disconnected but we have game state */}
      {showReconnecting && (
        <div className="mobile-reconnecting-overlay" data-testid="reconnecting-overlay">
          <div className="mobile-reconnecting-indicator">
            <div className="reconnecting-spinner" data-testid="reconnecting-spinner"></div>
            <span>Reconnecting...</span>
          </div>
        </div>
      )}

      {hasGameData && (
        <>
          {/* Header with MenuBar - matches menu screens */}
          <MenuBar
            onBack={onBack}
            centerContent={menuBarCenter}
            showUserInfo
            onAdminTools={navigateToAdmin}
            coachEnabled={coachEnabled}
            onCoachToggle={isGuest ? undefined : handleCoachToggle}
          />
          {/* Spacer for fixed MenuBar */}
          <div className="menu-bar-spacer" />

          {/* Cash mode bust modal — fires when server emits cash_bust /
           *  cash_rebuy_needed. Sits above MobileCashSheet so the player
           *  can't dismiss it by tapping outside. */}
          <BustModal event={cashBustEvent} onDismiss={clearCashBustEvent} />

          {/* Cash mode "everyone left" prompt — fires when the table
           *  empties of opponents but the human still has chips (paused
           *  server-side). Stay & play reseats; Return to lobby cashes out. */}
          <SoloTableModal cashMode={cashMode} />

          {/* Character dossier — opens when tapping an opponent avatar
           *  (when LLM debug isn't enabled). Uses the live Player blob
           *  for the basics; the personality block isn't loaded here so
           *  trait sections drop silently. */}
          <CharacterDetailCard
            isOpen={dossierPlayer !== null}
            onClose={closeDossier}
            character={dossierPlayer ? dossierFromPlayer(dossierPlayer) : { name: '' }}
            origin={dossierOrigin}
            identifier={dossierPlayer?.name}
          />

          {/* Cash mode: slide-up sheet — opens from the button inside
           *  the hero panel. Renders nothing for tournament games. */}
          {cashMode && humanPlayer && (
            <MobileCashSheet
              isOpen={showCashSheet}
              onClose={closeCashSheet}
              cashMode={cashMode}
              playerStack={humanPlayer.stack}
              handInProgress={
                phase !== 'INITIALIZING_HAND' &&
                phase !== 'HAND_OVER' &&
                phase !== 'EVALUATING_HAND'
              }
            />
          )}

          {/* Opponents Section */}
          <MobileOpponents
            opponents={opponents}
            activeOpponents={activeOpponents}
            foldedOpponents={foldedOpponents}
            isInShowdown={!!isInShowdown}
            isShowdown={isShowdown}
            storePlayers={storePlayers!}
            currentPlayerIdx={currentPlayerIdx}
            dealerIdx={dealerIdx}
            shouldHighlightActivePlayer={shouldHighlightActivePlayer}
            aiThinking={aiThinking}
            isHeadsUp={isHeadsUp}
            isTwoOpponents={isTwoOpponents}
            isThreeOpponents={isThreeOpponents}
            isThreeOpponentsNormal={isThreeOpponentsNormal}
            isThreeOpponentsShowdown={!!isThreeOpponentsShowdown}
            headsUpOpponent={headsUpOpponent}
            providedGameId={providedGameId}
            humanPlayerName={humanPlayer?.name}
            displayNickname={displayNickname}
            revealedCards={revealedCards}
            revealOrder={revealOrder}
            lastKnownActions={lastKnownActions}
            onFadeComplete={handleFadeComplete}
            containerRef={opponentsContainerRef}
            opponentRefs={opponentRefs}
            onOpenDebug={setDebugModalPlayer}
            onOpenDossier={openDossierForPlayer}
          />

          {/* Floating Pot Display - between opponents and community cards */}
          <div className="mobile-floating-pot" data-testid="mobile-pot">
            <PotDisplay total={pot!.total} />
          </div>

          {/* Community Cards - Always show 5 slots */}
          <MobileCommunityCards
            communityCards={communityCards}
            animations={communityCardAnimations}
          />

          {/* Floating AI Message. Sal "The Clock" gets special treatment — his
              lines are routed to the SalFloater queue (handleNewAiMessage) and
              never reach recentAiMessage, so FloatingChat only shows everyone else. */}
          <FloatingChat
            message={recentAiMessage}
            onDismiss={dismissRecentAiMessage}
            playerAvatars={playerAvatars}
          />
          <SalFloater queue={salQueue} onShown={dismissSalMessage} />

          {/* Hero Section - Your Cards */}
          <MobileHero
            humanPlayer={humanPlayer}
            currentPlayerIsHuman={!!currentPlayer?.is_human}
            cashMode={cashMode}
            onOpenCash={openCashSheet}
            isHumanDealer={storePlayers?.findIndex((p) => p.is_human) === dealerIdx}
            heroCommitted={heroCommitted}
            heroRetreating={heroRetreating}
            isExiting={isExiting}
            isDealing={isDealing}
            displayCards={displayCards}
            cardTransforms={cardTransforms}
            cardsNeat={cardsNeat}
            toggleCardsNeat={toggleCardsNeat}
            onExitAnimationEnd={handleExitAnimationEnd}
          />

          {/* Action Buttons - Always visible area */}
          <MobileActionArea
            showActionButtons={!!showActionButtons}
            currentPlayer={currentPlayer}
            hasWinner={!!winnerInfo}
            isShuffling={isShuffling}
            playerOptions={playerOptions}
            highestBet={highestBet}
            minRaise={minRaise}
            bigBlind={bigBlind}
            potTotal={pot!.total}
            onAction={handlePlayerAction}
            onQuickChat={openChatSheet}
            bettingContext={bettingContext}
            recommendedAction={recommendedAction}
            raiseToAmount={raiseToAmount}
            humanPlayer={humanPlayer}
            playerName={playerName}
            aiThinking={aiThinking}
            queuedAction={queuedAction}
            setQueuedAction={setQueuedAction}
            gameId={gameId}
            aiInstant={aiInstant}
            alwaysFastForward={alwaysFastForward}
            fastForward={fastForward}
          />

          {/* Winner Announcement — the "result" beat for showdown wins only.
          Fold-out walks stay uneventful (their winner line shows in the shuffle
          screen below). The sequencer only sets winnerInfo once the actions /
          board / run-out reactions have drained, so mounting on it directly is
          already correctly ordered — no separate reveal gate needed. When the
          overlay's hold elapses or the player taps Continue, handleResultComplete
          hands off to the shuffle beat and clears the winner. */}
          {/* The human is identified from the players list's is_human seat
              inside MobileWinnerAnnouncement; playerName is only a fallback. */}
          {winnerInfo && winnerInfo.showdown && (
            <MobileWinnerAnnouncement
              winnerInfo={winnerInfo}
              onComplete={handleResultComplete}
              gameId={gameId || ''}
              playerName={playerName || ''}
              onSendMessage={wrappedSendMessage}
              players={storePlayers || []}
            />
          )}

          {/* Tournament Complete — shown only after the hand presentation is
              fully done: the run-out sequencer has drained (`!isPlaying`) AND the
              winner overlay has been dismissed (`!winnerInfo`). Without the
              `isPlaying` guard the backend's synchronous `tournament_complete`
              (emitted at the hand boundary) flashes results over a still-
              animating final hand. */}
          {!winnerInfo && !isPlaying && (
            <TournamentComplete
              result={tournamentResult}
              onComplete={handleTournamentComplete}
              gameId={gameId || undefined}
              playerName={playerName}
              onSendMessage={wrappedSendMessage}
            />
          )}

          {/* Shuffle beat — owned by the director. For showdowns it follows the
          winner overlay; for fold-out walks it IS the result (the winner line
          shows in place of "Shuffling"). Either way it holds a client minimum so
          it can't flash, and covers the wait for the backend to deal the next
          hand. */}
          <ShuffleLoading
            isVisible={isShuffling}
            message={interhandMessage || 'Shuffling'}
            submessage={interhandSubmessage}
            handNumber={cashMode ? undefined : handNumber}
            ticker={interhandTicker}
            quote={interhandQuote}
            variant="interhand"
            showDots={!interhandMessage}
          />

          {/* Chat Sheet - Redesigned with tabs for Quick Chat / Keyboard */}
          <MobileChatSheet
            isOpen={showChatSheet}
            onClose={closeChatSheet}
            messages={messages}
            onSendMessage={wrappedSendMessage}
            gameId={providedGameId || ''}
            playerName={playerName || 'Player'}
            players={storePlayers || []}
            guestChatDisabled={guestChatDisabled}
            guestFreeChatLocked={guestFreeChatLocked}
          />

          {/* LLM Debug Modal */}
          <LLMDebugModal
            isOpen={!!debugModalPlayer}
            onClose={closeDebugModal}
            playerName={debugModalPlayer?.name || ''}
            debugInfo={debugModalPlayer?.llm_debug}
          />

          {/* Coach Components */}
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

              <CoachPanel
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

          {/* Guest Hand Limit Modal */}
          {guestLimitReached && usageStats && (
            <GuestLimitModal
              handsPlayed={usageStats.hands_played}
              handsLimit={usageStats.hands_limit}
              onReturnToMenu={() => {
                if (onBack) onBack();
              }}
            />
          )}
        </>
      )}
    </div>
  );
}
