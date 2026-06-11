import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import toast from 'react-hot-toast';
import { CharacterDetailCard } from '../../character';
import { dossierFromPlayer } from '../../character/dossierFromPlayer';
import { LLMDebugModal } from '../../mobile/LLMDebugModal';
import { CoachButton } from '../../mobile/CoachButton';
import { CoachBubble } from '../../mobile/CoachBubble';
import { CoachDock } from '../CoachDock';
import { HeadsUpOpponentPanel } from '../../mobile/HeadsUpOpponentPanel';
import { PlayerSeat } from './PlayerSeat';
import { CommunityBoard } from './CommunityBoard';
import { ShowdownGhostRail } from './ShowdownGhostRail';
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
import { GuestLimitModal, FloatingUserMenu } from '../../shared';
import { ShuffleLoading, type TickerLine } from '../../shared/ShuffleLoading';
import { selectInterhandTicker } from '../../cash/interhandTicker';
import { feedEventKey, renderEventIcon } from '../../cash/tickerEvents';
import { useUsageStats } from '../../../hooks/useUsageStats';
import { useInterhandDirector } from '../../../hooks/useInterhandDirector';
import { useGameStore } from '../../../stores/gameStore';
import { pickQuote } from '../WinnerAnnouncement/quote-flavor';
import { useGuestChatLimit } from '../../../hooks/useGuestChatLimit';
import { useCoach } from '../../../hooks/useCoach';
import { logger } from '../../../utils/logger';
import { gameAPI } from '../../../utils/api';
import { config } from '../../../config';
import { usePokerGame } from '../../../hooks/usePokerGame';
import { useTournamentEvents } from '../../../hooks/useTournamentEvents';
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
  /** Parent's back handler. Falls back to `window.location.href = '/menu'`
   *  if omitted (full reload back to the menu, resetting game state). */
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
  // Stable so the memoized PlayerSeat (and its ActionBadge) don't re-render every
  // tick — an inline arrow here would defeat their memoization for every seat.
  const handleFadeComplete = useCallback(() => setFadeKey((k) => k + 1), []);

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
    heroCommitted,
    heroRetreating,
    isPlaying,
    tournamentResult,
    socketRef,
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

  // Multi-table tournament felt: relocation toasts + bust/win routing to the
  // standings hub (no-op for non-tournament games). See useTournamentEvents.
  useTournamentEvents({ socketRef, connected: isConnected, gameId });

  // Community-card deal-in animation timing (flop cascade, turn/river single).
  const communityCardAnimations = useCommunityCardAnimation(
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

  // The run-out reactions + hero card-commit gesture (heroCommitted /
  // heroRetreating, above) are now owned by the hand sequencer in usePokerGame,
  // shared by both tables — no separate desktop run-out director.

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
    // Navigate back to menu by reloading (full reload resets game state).
    window.location.href = '/menu';
  }, [gameId, clearTournamentResult]);

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
      <CommunityBoard
        potTotal={gameState.pot.total}
        communityCards={gameState.community_cards}
        animations={communityCardAnimations}
      />

      {/* Winner Announcement */}
      {/* The human is identified from the players list's is_human seat inside
          WinnerAnnouncement; playerName is only a fallback. */}
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

      {/* Tournament Complete — held until the hand presentation is fully done:
          the run-out sequencer has drained (`!isPlaying`) AND the winner overlay
          has been dismissed (`!winnerInfo`). Otherwise the backend's synchronous
          `tournament_complete` (emitted at the hand boundary) would flash the
          results over a still-animating final hand. */}
      {!winnerInfo && !isPlaying && (
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
      {/* Floating avatar menu — the desktop game's only route into Settings /
          Main Menu / Admin Tools. Records this game as the admin return origin. */}
      {providedGameId && <FloatingUserMenu returnTo={`/game/${providedGameId}`} />}
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
                window.location.href = '/menu';
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
                const isCurrentPlayer =
                  shouldHighlightActivePlayer && playerIndex === gameState.current_player_idx;
                const headsUpShowdownSlot = isHeadsUpShowdownLayout
                  ? showdownOpponents.findIndex((p: Player) => p.name === player.name)
                  : -1;

                return (
                  <PlayerSeat
                    key={player.name}
                    player={player}
                    displayName={displayNickname(player)}
                    seatOffset={seatOffset}
                    totalPlayers={totalPlayers}
                    headsUpShowdownSlot={headsUpShowdownSlot >= 0 ? headsUpShowdownSlot : undefined}
                    isDealer={playerIndex === gameState.current_dealer_idx}
                    isSmallBlind={playerIndex === gameState.small_blind_idx}
                    isBigBlind={playerIndex === gameState.big_blind_idx}
                    isCurrentPlayer={isCurrentPlayer}
                    aiThinking={aiThinking}
                    isSpeaking={recentAiMessage?.sender === player.name}
                    speechMessage={recentAiMessage}
                    revealedCards={revealedCards?.players_cards[player.name]}
                    revealIndex={revealOrder.get(player.name) ?? 0}
                    lastKnownActions={lastKnownActions}
                    onOpenDossier={openDossierForPlayer}
                    onFadeComplete={handleFadeComplete}
                    onDismissSpeech={dismissRecentAiMessage}
                  />
                );
              })}
            </div>

            {/* Folded opponents shrink to a top-left ghost rail during showdown. */}
            {isInShowdown && (
              <ShowdownGhostRail foldedOpponents={foldedOpponents} displayName={displayNickname} />
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
