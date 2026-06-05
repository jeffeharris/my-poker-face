import { useEffect, useState, useRef, useCallback, useMemo } from 'react';
import toast from 'react-hot-toast';
import { useGuestChatLimit } from '../../hooks/useGuestChatLimit';
import type { ChatMessage } from '../../types';
import type { Player } from '../../types/player';
import { MobileOpponents } from './MobileOpponents';
import { MobileCommunityCards } from './MobileCommunityCards';
import { MobileHero } from './MobileHero';
import { MobileActionArea } from './MobileActionArea';
import { FloatingChat } from './FloatingChat';
import { MobileWinnerAnnouncement } from './MobileWinnerAnnouncement';
import { TournamentComplete } from '../game/TournamentComplete';
import { MobileChatSheet } from './MobileChatSheet';
import { ShuffleLoading, type TickerLine } from '../shared/ShuffleLoading';
import { selectInterhandTicker } from '../cash/interhandTicker';
import { feedEventKey, renderEventIcon } from '../cash/tickerEvents';
import { pickQuote } from '../game/WinnerAnnouncement/quote-flavor';
import { GuestLimitModal } from '../shared';
import { useUsageStats } from '../../hooks/useUsageStats';
import { LLMDebugModal } from './LLMDebugModal';
import { CoachButton } from './CoachButton';
import { CoachPanel } from './CoachPanel';
import { CoachBubble } from './CoachBubble';
import { MobileCashSheet } from '../cash/MobileCashSheet';
import { BustModal } from '../cash/BustModal';
import { SoloTableModal } from '../cash/SoloTableModal';
import { CharacterDetailCard } from '../character';
import { dossierFromPlayer } from '../character/dossierFromPlayer';
import { MenuBar, PotDisplay, GameInfoDisplay } from '../shared';
import { usePokerGame } from '../../hooks/usePokerGame';
import { useTournamentEvents } from '../../hooks/useTournamentEvents';
import { useGameStore } from '../../stores/gameStore';
import { useDisplayNickname } from '../../stores/nicknameOverridesStore';
import { useCardAnimation } from '../../hooks/useCardAnimation';
import { useCommunityCardAnimation } from '../../hooks/useCommunityCardAnimation';
import { useCoach } from '../../hooks/useCoach';
import { useInterhandDirector } from '../../hooks/useInterhandDirector';
import { isBettingPhase } from '../../constants/gamePhases';
import { orderOpponentsRelativeToHuman } from '../../utils/playerOrdering';
import { logger } from '../../utils/logger';
import { avatarUrlForEmotion } from '../../utils/avatarUrl';
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

// How many world-ticker beats the interhand "meanwhile, elsewhere" strip
// shows at once. A few of the biggest/rarest — not a full feed.
const MAX_INTERHAND_TICKER = 3;

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
  const opponentsContainerRef = useRef<HTMLDivElement>(null);
  const opponentRefs = useRef<Map<string, HTMLDivElement>>(new Map());

  // Track last known actions for fade-out animation
  const lastKnownActions = useRef<Map<string, string>>(new Map());
  // Incrementing this state forces a re-render after the ref is mutated on fade completion
  const [, setFadeKey] = useState(0);

  // Callbacks for handling AI messages (for floating bubbles)
  const handleNewAiMessage = useCallback((message: ChatMessage) => {
    setRecentAiMessage(message);
  }, []);

  const dismissRecentAiMessage = useCallback(() => {
    setRecentAiMessage(null);
  }, []);

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

  // When the chat sheet is opened from the dossier ("Send chat" button),
  // remember which player to pre-select as the target. Cleared when the
  // sheet closes so a fresh open from the chat button starts at "table".
  const [chatInitialTarget, setChatInitialTarget] = useState<string | null>(null);

  // Stable callbacks for child components to avoid re-renders
  const openChatSheet = useCallback(() => setShowChatSheet(true), []);
  const closeChatSheet = useCallback(() => {
    setShowChatSheet(false);
    setChatInitialTarget(null);
  }, []);
  const openChatWithTarget = useCallback((targetName: string) => {
    setChatInitialTarget(targetName);
    setDossierPlayer(null);
    setShowChatSheet(true);
  }, []);
  const openCashSheet = useCallback(() => setShowCashSheet(true), []);
  const closeCashSheet = useCallback(() => setShowCashSheet(false), []);
  const openCoachPanel = useCallback(() => setShowCoachPanel(true), []);
  const closeCoachPanel = useCallback(() => setShowCoachPanel(false), []);
  const closeDebugModal = useCallback(() => setDebugModalPlayer(null), []);
  const navigateToAdmin = useCallback(() => {
    window.location.href = '/admin';
  }, []);
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

  // Pick a flavor quote for the interhand shuffle. Memoized by handNumber so
  // it stays stable across re-renders during a single shuffle and changes
  // each hand.
  const interhandQuote = useMemo(() => {
    const q = pickQuote('between_hands');
    return q ? { text: q.text, attribution: q.attribution } : undefined;
    // handNumber is an intentional recompute key (not read inside): it re-picks
    // the random quote each new hand while staying stable on re-renders.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [handNumber]);

  // Cash/career mode: turn the interhand pause into a "meanwhile, elsewhere"
  // world ticker — the bigger, rarer beats from around the room since this
  // hand started (events tagged with the hand that just ended), minus routine
  // sit-downs/leaves. `undefined` in tournament mode, where the world isn't
  // simulated and the hand-number badge stays.
  const interhandTicker = useMemo<TickerLine[] | undefined>(() => {
    if (!cashMode) return undefined;
    const thisHand = worldEvents.filter((w) => w.hand === handNumber).map((w) => w.event);
    return selectInterhandTicker(thisHand, MAX_INTERHAND_TICKER).map((e) => ({
      key: feedEventKey(e),
      icon: renderEventIcon(e.type),
      message: e.message,
    }));
  }, [cashMode, worldEvents, handNumber]);

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

  // Fold-out (walk) wins are intentionally uneventful: no winner overlay, just
  // the shuffle screen with the winner line in place of "Shuffling". Capture
  // that line, hand straight off to the director's shuffle beat (whose minimum
  // floor keeps it from flashing), and clear winnerInfo so the showdown overlay
  // never mounts for a walk.
  const [interhandMessage, setInterhandMessage] = useState<string | null>(null);
  const [interhandSubmessage, setInterhandSubmessage] = useState<string | undefined>(undefined);

  useEffect(() => {
    if (!winnerInfo || winnerInfo.showdown) return;
    // Compute net profit (gross winnings minus what the winner put in)
    let netProfit: number | null = null;
    if (winnerInfo.pot_breakdown) {
      const gross = winnerInfo.pot_breakdown.reduce(
        (sum, pot) => sum + pot.winners.reduce((s, w) => s + w.amount, 0),
        0
      );
      const contributions = winnerInfo.pot_contributions ?? {};
      const winnerContrib = winnerInfo.winners.reduce(
        (sum, name) => sum + (contributions[name] ?? 0),
        0
      );
      netProfit = gross - winnerContrib;
    }
    const names =
      winnerInfo.winners.length > 1 ? winnerInfo.winners.join(' & ') : winnerInfo.winners[0];
    const verb = winnerInfo.winners.length > 1 ? 'SPLIT' : 'WON';
    // Name on its own line (the hero); the amount drops to the line below as
    // "WON $X" — no animated dots, since the hand is finished, not loading.
    setInterhandMessage(names);
    setInterhandSubmessage(
      netProfit != null && netProfit > 0 ? `${verb} $${netProfit.toLocaleString()}` : verb
    );

    if (!winnerInfo.is_final_hand) {
      beginShuffle();
    }
    clearWinnerInfo();
  }, [winnerInfo, clearWinnerInfo, beginShuffle]);

  // Clear the walk message once the next hand starts.
  useEffect(() => {
    setInterhandMessage(null);
    setInterhandSubmessage(undefined);
  }, [handNumber]);

  // Coach hook
  const coach = useCoach({
    gameId: providedGameId ?? null,
    playerName: playerName || '',
    isPlayerTurn: !!showActionButtons,
  });

  const coachEnabled = !isGuest && coach.mode !== 'off';

  // Memoize coach recommendation values to prevent unnecessary re-renders of MobileActionButtons
  // - Proactive mode: Show coach's recommendation after proactive tip (coachAction)
  // - Reactive mode: Only show recommendation after player asks a question (coachAction)
  // - Off mode: No highlighting
  const recommendedAction = coach.mode === 'off' ? null : coach.coachAction;
  const raiseToAmount = coach.mode === 'off' ? null : coach.coachRaiseTo;

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

  const handleCoachToggle = useCallback(() => {
    try {
      if (coachEnabled) {
        // Save current mode before turning off
        localStorage.setItem('coach_mode_before_off', coach.mode);
        coach.setMode('off');
      } else {
        // Restore previous mode
        const previous = localStorage.getItem('coach_mode_before_off');
        coach.setMode(previous === 'proactive' || previous === 'reactive' ? previous : 'reactive');
      }
    } catch (err) {
      logger.warn('localStorage unavailable for coach mode toggle:', err);
      coach.setMode(coachEnabled ? 'off' : 'reactive');
    }
  }, [coachEnabled, coach]);

  // When a hand ends, request a post-hand review from the coach.
  // coach.mode is omitted: we only want to trigger on winnerInfo change,
  // not re-fire when mode toggles while a winner banner is showing.
  useEffect(() => {
    if (winnerInfo && coach.mode !== 'off') {
      coach.fetchHandReview();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [winnerInfo, coach.fetchHandReview]);

  // Clear unread review indicator when coach panel is opened.
  // coach.hasUnreadReview is omitted: we only want to clear when the panel
  // opens, not re-fire when a new review arrives while the panel is already open.
  useEffect(() => {
    if (showCoachPanel && coach.hasUnreadReview) {
      coach.clearUnreadReview();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [showCoachPanel, coach.clearUnreadReview]);

  // Skill unlock toasts — show all staggered, then dismiss entire batch
  useEffect(() => {
    if (coach.skillUnlockQueue.length === 0) return;

    // Snapshot the queue and dismiss immediately so the effect won't re-fire
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
            onSendChat={openChatWithTarget}
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

          {/* Floating AI Message */}
          <FloatingChat
            message={recentAiMessage}
            onDismiss={dismissRecentAiMessage}
            playerAvatars={playerAvatars}
          />

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

          {/* Tournament Complete - only show when winner announcement is dismissed */}
          {/* This ensures winner announcement is ALWAYS shown first, then tournament complete after */}
          {!winnerInfo && (
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
            initialTarget={chatInitialTarget}
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
