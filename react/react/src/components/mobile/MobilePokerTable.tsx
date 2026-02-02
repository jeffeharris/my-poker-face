import { useEffect, useState, useRef, useCallback } from 'react';
import { useGuestChatLimit } from '../../hooks/useGuestChatLimit';
import { Check, X, MessageCircle } from 'lucide-react';
import type { ChatMessage } from '../../types';
import type { Player } from '../../types/player';
import { Card } from '../cards';
import { MobileActionButtons } from './MobileActionButtons';
import { FloatingChat } from './FloatingChat';
import { MobileWinnerAnnouncement } from './MobileWinnerAnnouncement';
import { TournamentComplete } from '../game/TournamentComplete';
import { MobileChatSheet } from './MobileChatSheet';
import { GuestLimitModal } from '../shared';
import { useUsageStats } from '../../hooks/useUsageStats';
import { HeadsUpOpponentPanel } from './HeadsUpOpponentPanel';
import { LLMDebugModal } from './LLMDebugModal';
import { CoachButton } from './CoachButton';
import { CoachPanel } from './CoachPanel';
import { CoachBubble } from './CoachBubble';
import { MenuBar, PotDisplay, GameInfoDisplay, ActionBadge } from '../shared';
import { usePokerGame } from '../../hooks/usePokerGame';
import { useCardAnimation } from '../../hooks/useCardAnimation';
import { useCommunityCardAnimation } from '../../hooks/useCommunityCardAnimation';
import { useCoach } from '../../hooks/useCoach';
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
}

export function MobilePokerTable({
  gameId: providedGameId,
  playerName,
  onGameCreated,
  onBack
}: MobilePokerTableProps) {
  // Mobile-specific state
  const [showChatSheet, setShowChatSheet] = useState(false);
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

  // Use the shared hook for all socket/state management
  const {
    gameState,
    loading,
    gameId,
    messages,
    aiThinking,
    winnerInfo,
    revealedCards,
    tournamentResult,
    isConnected,
    showActionButtons,
    queuedAction,
    setQueuedAction,
    handlePlayerAction,
    handleSendMessage,
    clearWinnerInfo,
    clearTournamentResult,
    guestLimitReached,
  } = usePokerGame({
    gameId: providedGameId ?? null,
    playerName,
    onGameCreated,
    onNewAiMessage: handleNewAiMessage,
  });

  const { wrappedSendMessage, guestChatDisabled, isGuest } = useGuestChatLimit(
    gameState?.awaiting_action,
    handleSendMessage,
  );

  // Usage stats for guest limit modal
  const { stats: usageStats } = useUsageStats();

  // Handle tournament completion - clean up and return to menu
  const handleTournamentComplete = useCallback(async () => {
    if (gameId) {
      try {
        await fetch(`${config.API_URL}/api/end_game/${gameId}`, {
          method: 'POST',
          credentials: 'include',
        });
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

  const currentPlayer = gameState?.players[gameState.current_player_idx];
  const humanPlayer = gameState?.players.find(p => p.is_human);
  const isShowdown = gameState?.phase?.toLowerCase() === 'showdown';

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
  const communityCardAnimations = useCommunityCardAnimation(
    gameState?.newly_dealt_count,
    gameState?.community_cards?.length ?? 0,
  );

  // Auto-scroll to center the active opponent when turn changes
  useEffect(() => {
    if (!gameState || !currentPlayer || currentPlayer.is_human) return;

    const opponentEl = opponentRefs.current.get(currentPlayer.name);
    const containerEl = opponentsContainerRef.current;

    if (opponentEl && containerEl) {
      // Wait for CSS width transition to complete (300ms) before centering
      const timeoutId = setTimeout(() => {
        // Calculate scroll position to center the element with its new width
        const containerWidth = containerEl.offsetWidth;
        const elementLeft = opponentEl.offsetLeft;
        const elementWidth = opponentEl.offsetWidth;
        const scrollTarget = elementLeft - (containerWidth / 2) + (elementWidth / 2);

        containerEl.scrollTo({
          left: scrollTarget,
          behavior: 'smooth'
        });
      }, 320); // Slightly longer than 300ms transition

      return () => clearTimeout(timeoutId);
    }
    // Only re-run when current player changes, not on every gameState update
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [gameState?.current_player_idx, currentPlayer?.name]);

  // Sort opponents by their position relative to the human player in turn order
  const opponents = (() => {
    if (!gameState?.players) return [];
    const humanIndex = gameState.players.findIndex(p => p.is_human);
    const totalPlayers = gameState.players.length;

    return gameState.players
      .filter(p => !p.is_human)
      .sort((a, b) => {
        const idxA = gameState.players.findIndex(p => p.name === a.name);
        const idxB = gameState.players.findIndex(p => p.name === b.name);

        // Calculate clockwise distance from human (wrapping around)
        const distA = (idxA - humanIndex + totalPlayers) % totalPlayers;
        const distB = (idxB - humanIndex + totalPlayers) % totalPlayers;

        return distA - distB;
      });
  })();

  // Heads-up mode: only 1 AI opponent remains
  const isHeadsUp = opponents.length === 1;
  const headsUpOpponent = isHeadsUp ? opponents[0] : null;

  // Two opponents mode: 2 AI opponents (3 players total)
  const isTwoOpponents = opponents.length === 2;

  // Coach hook
  const coach = useCoach({
    gameId: providedGameId ?? null,
    playerName: playerName || '',
    isPlayerTurn: !!showActionButtons,
  });

  const coachEnabled = !isGuest && coach.mode !== 'off';

  const handleCoachToggle = useCallback(() => {
    try {
      if (coachEnabled) {
        // Save current mode before turning off
        localStorage.setItem('coach_mode_before_off', coach.mode);
        coach.setMode('off');
      } else {
        // Restore previous mode
        const previous = localStorage.getItem('coach_mode_before_off');
        coach.setMode((previous === 'proactive' || previous === 'reactive') ? previous : 'reactive');
      }
    } catch {
      // localStorage unavailable — just toggle mode without persisting
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
  // Only show full loading screen on initial load (no game state yet)
  // If we have game state but are disconnected, we'll show a reconnecting overlay instead
  if (loading && !gameState) {
    return (
      <div className="mobile-poker-table mobile-loading" data-testid="mobile-loading">
        <div className="mobile-loading-content">
          <div className="loading-cards">
            {['♠', '♥', '♦', '♣'].map((suit, i) => (
              <div key={i} className={`loading-card suit-${i}`}>{suit}</div>
            ))}
          </div>
          <p>Setting up the table...</p>
        </div>
      </div>
    );
  }

  if (!gameState) {
    return <div className="mobile-poker-table mobile-error">Failed to load game</div>;
  }

  // Show reconnecting indicator when disconnected but we still have game state
  const showReconnecting = !isConnected && gameState;

  return (
    <div className="mobile-poker-table" data-testid="mobile-poker-table">
      {/* Reconnecting overlay - shows when socket is disconnected but we have game state */}
      {showReconnecting && (
        <div className="mobile-reconnecting-overlay" data-testid="reconnecting-overlay">
          <div className="mobile-reconnecting-indicator">
            <div className="reconnecting-spinner" data-testid="reconnecting-spinner"></div>
            <span>Reconnecting...</span>
          </div>
        </div>
      )}

      {/* Header with MenuBar - matches menu screens */}
      <MenuBar
        onBack={onBack}
        centerContent={
          <GameInfoDisplay
            phase={gameState.phase}
            smallBlind={gameState.small_blind}
            bigBlind={gameState.big_blind}
            handNumber={gameState.hand_number}
          />
        }
        showUserInfo
        onAdminTools={() => { window.location.href = '/admin'; }}
        coachEnabled={coachEnabled}
        onCoachToggle={isGuest ? undefined : handleCoachToggle}
      />
      {/* Spacer for fixed MenuBar */}
      <div className="menu-bar-spacer" />

      {/* Opponents Strip */}
      <div className={`mobile-opponents ${isHeadsUp ? 'heads-up-mode' : ''} ${isTwoOpponents ? 'two-opponents-mode' : ''}`} data-testid="mobile-opponents" ref={opponentsContainerRef}>
        {opponents.map((opponent) => {
          const opponentIdx = gameState.players.findIndex(p => p.name === opponent.name);
          const isCurrentPlayer = opponentIdx === gameState.current_player_idx;
          const isDealer = opponentIdx === gameState.current_dealer_idx;

          return (
            <div
              key={opponent.name}
              ref={(el) => {
                if (el) {
                  opponentRefs.current.set(opponent.name, el);
                } else {
                  opponentRefs.current.delete(opponent.name);
                }
              }}
              className={`mobile-opponent ${opponent.is_folded ? 'folded' : ''} ${opponent.is_all_in ? 'all-in' : ''} ${isCurrentPlayer ? 'thinking' : ''} ${isHeadsUp ? 'heads-up-avatar' : ''} ${isTwoOpponents ? 'two-opponents-avatar' : ''}`}
              data-testid="mobile-opponent"
            >
              <div
                className={`opponent-avatar ${config.ENABLE_AI_DEBUG && opponent.llm_debug ? 'debug-enabled' : ''}`}
                onClick={config.ENABLE_AI_DEBUG && opponent.llm_debug ? () => setDebugModalPlayer(opponent) : undefined}
                role={config.ENABLE_AI_DEBUG && opponent.llm_debug ? 'button' : undefined}
                tabIndex={config.ENABLE_AI_DEBUG && opponent.llm_debug ? 0 : undefined}
                aria-label={config.ENABLE_AI_DEBUG && opponent.llm_debug ? `View ${opponent.name}'s AI model info` : undefined}
              >
                {opponent.avatar_url ? (
                  <img
                    src={`${config.API_URL}${opponent.avatar_url}`}
                    alt={`${opponent.name} - ${opponent.avatar_emotion || 'avatar'}`}
                    className={`avatar-image ${
                      opponent.avatar_emotion === 'thinking' ? 'avatar-image--thinking' : ''
                    } ${isShowdown ? 'avatar-image--showdown' : ''}`}
                    onError={(e) => {
                      // Hide broken image if avatar fails to load
                      const img = e.currentTarget;
                      img.style.display = 'none';
                    }}
                  />
                ) : (
                  opponent.name.charAt(0).toUpperCase()
                )}
                {isDealer && <span className="dealer-badge">D</span>}
                {/* Debug indicator badge */}
                {config.ENABLE_AI_DEBUG && opponent.llm_debug && (
                  <span className="debug-badge" title="Tap to view AI model info"></span>
                )}
              </div>
              <div className="opponent-info">
                <span className="opponent-name" data-testid="opponent-name">{opponent.nickname || opponent.name}</span>
                <span className="opponent-stack" data-testid="opponent-stack">${opponent.stack}</span>
              </div>
              {opponent.bet > 0 && (
                <div className="opponent-bet">${opponent.bet}</div>
              )}
              {/* Revealed hole cards during run-it-out showdown */}
              {revealedCards?.players_cards[opponent.name] && (
                <div className="opponent-revealed-cards">
                  {revealedCards.players_cards[opponent.name].map((card, i) => (
                    <Card key={i} card={card} faceDown={false} size="large" />
                  ))}
                </div>
              )}
              <ActionBadge
                player={opponent}
                lastKnownActions={lastKnownActions}
                onFadeComplete={() => setFadeKey(k => k + 1)}
              />
            </div>
          );
        })}

        {/* Heads-up psychology panel */}
        {isHeadsUp && headsUpOpponent && providedGameId && (
          <HeadsUpOpponentPanel
            opponent={headsUpOpponent}
            gameId={providedGameId}
            humanPlayerName={humanPlayer?.name}
          />
        )}
      </div>

      {/* Floating Pot Display - between opponents and community cards */}
      <div className="mobile-floating-pot" data-testid="mobile-pot">
        <PotDisplay total={gameState.pot.total} />
      </div>

      {/* Community Cards - Always show 5 slots */}
      <div className="mobile-community" data-testid="mobile-community">
        <div className="community-cards-row">
          {Array.from({ length: 5 }).map((_, i) => {
            const card = gameState.community_cards[i];
            const anim = communityCardAnimations[i];
            const isDealt = !!card;
            const isAnimating = anim?.shouldAnimate;
            return (
              <div key={i} className="community-card-slot">
                {/* Placeholder fades out when card arrives */}
                <div className={`community-card-placeholder ${isDealt ? (isAnimating ? 'fade-out-delayed' : 'hidden') : ''}`}
                  style={isAnimating ? { animationDelay: `${anim.delay + anim.duration * 0.6}s` } : undefined}
                />
                {/* Card overlays placeholder */}
                {isDealt && (
                  <div
                    className="community-card-overlay"
                    style={isAnimating ? {
                      animation: `communityCardDealIn ${anim.duration}s cubic-bezier(0.16, 1, 0.3, 1) ${anim.delay}s both`,
                    } : undefined}
                  >
                    <Card card={card} faceDown={false} size="medium" />
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>

      {/* Floating AI Message */}
      <FloatingChat
        message={recentAiMessage}
        onDismiss={dismissRecentAiMessage}
        players={gameState.players}
      />

      {/* Hero Section - Your Cards */}
      <div className={`mobile-hero ${currentPlayer?.is_human ? 'active-turn' : ''} ${humanPlayer?.is_folded ? 'folded' : ''}`} data-testid="mobile-hero">
        {/* Dealer chip - positioned in upper right */}
        {gameState.players.findIndex(p => p.is_human) === gameState.current_dealer_idx && (
          <span className="dealer-chip">D</span>
        )}
        <div className="hero-info">
          <div className="hero-name">{humanPlayer?.name}</div>
          <div className="hero-stack">${humanPlayer?.stack}</div>
        </div>
        {/* Bet chip - positioned at top edge of hero section */}
        {humanPlayer && humanPlayer.bet > 0 && (
          <div className="hero-bet">${humanPlayer.bet}</div>
        )}
        <div className="hero-cards" data-testid="hero-cards" style={{ gap: `${cardTransforms.gap}px`, transition: cardsNeat ? 'gap 0.2s ease-out' : 'none' }}>
          {isExiting && displayCards?.[0] && displayCards?.[1] ? (
            /* Exit animation - cards sweep off, then onAnimationEnd triggers new cards */
            <>
              <div
                style={{
                  animation: `dealCardOut1 0.45s cubic-bezier(0.4, 0, 1, 1) forwards`,
                  '--exit-start-x': `${cardTransforms.card1.offsetX}px`,
                  '--exit-start-y': `${cardTransforms.card1.offsetY}px`,
                  '--exit-start-rotation': `${cardTransforms.card1.rotation}deg`,
                  '--exit-converge-x': `${cardTransforms.card2.offsetX + cardTransforms.gap}px`,
                } as React.CSSProperties}
              >
                <Card card={displayCards[0]} faceDown={false} size="xlarge" className="hero-card" />
              </div>
              <div
                onAnimationEnd={handleExitAnimationEnd}
                style={{
                  animation: `dealCardOut2 0.45s cubic-bezier(0.4, 0, 1, 1) forwards`,
                  '--exit-start-x': `${cardTransforms.card2.offsetX}px`,
                  '--exit-start-y': `${cardTransforms.card2.offsetY}px`,
                  '--exit-start-rotation': `${cardTransforms.card2.rotation}deg`,
                } as React.CSSProperties}
              >
                <Card card={displayCards[1]} faceDown={false} size="xlarge" className="hero-card" />
              </div>
            </>
          ) : displayCards?.[0] && displayCards?.[1] ? (
            <>
              <div
                onClick={toggleCardsNeat}
                style={{
                  transform: `rotate(${cardTransforms.card1.rotation}deg) translateX(${cardTransforms.card1.offsetX}px) translateY(${cardTransforms.card1.offsetY}px)`,
                  transition: cardsNeat ? 'transform 0.2s ease-out' : 'none',
                  cursor: 'pointer',
                  animation: isDealing ? `dealCardIn 0.55s cubic-bezier(0.16, 1, 0.3, 1) both` : 'none',
                  opacity: humanPlayer?.is_folded ? 0.5 : 1,
                  '--deal-rotation': `${cardTransforms.card1.rotation}deg`,
                  '--deal-start-rotation': `${cardTransforms.card1.startRotation}deg`,
                  '--deal-offset-x': `${cardTransforms.card1.offsetX}px`,
                  '--deal-offset-y': `${cardTransforms.card1.offsetY}px`,
                } as React.CSSProperties}
              >
                <Card card={displayCards[0]} faceDown={false} size="xlarge" className="hero-card" />
              </div>
              <div
                onClick={toggleCardsNeat}
                style={{
                  transform: `rotate(${cardTransforms.card2.rotation}deg) translateX(${cardTransforms.card2.offsetX}px) translateY(${cardTransforms.card2.offsetY}px)`,
                  transition: cardsNeat ? 'transform 0.2s ease-out' : 'none',
                  cursor: 'pointer',
                  animation: isDealing ? `dealCardIn 0.55s cubic-bezier(0.16, 1, 0.3, 1) 0.15s both` : 'none',
                  opacity: humanPlayer?.is_folded ? 0.5 : 1,
                  '--deal-rotation': `${cardTransforms.card2.rotation}deg`,
                  '--deal-start-rotation': `${cardTransforms.card2.startRotation}deg`,
                  '--deal-offset-x': `${cardTransforms.card2.offsetX}px`,
                  '--deal-offset-y': `${cardTransforms.card2.offsetY}px`,
                } as React.CSSProperties}
              >
                <Card card={displayCards[1]} faceDown={false} size="xlarge" className="hero-card" />
              </div>
            </>
          ) : (
            <>
              <div className="card-placeholder" />
              <div className="card-placeholder" />
            </>
          )}
        </div>

      </div>

      {/* Action Buttons - Always visible area */}
      <div className="mobile-action-area">
        {showActionButtons && currentPlayer ? (
          <MobileActionButtons
            playerOptions={gameState.player_options}
            currentPlayerStack={currentPlayer.stack}
            highestBet={gameState.highest_bet}
            currentPlayerBet={currentPlayer.bet}
            minRaise={gameState.min_raise}
            bigBlind={gameState.big_blind}
            potSize={gameState.pot.total}
            onAction={handlePlayerAction}
            onQuickChat={() => setShowChatSheet(true)}
            bettingContext={gameState.betting_context}
            recommendedAction={coach.mode !== 'off' ? coach.stats?.recommendation : null}
          />
        ) : (
          <div className="mobile-action-buttons">
            {/* Preemptive Check/Fold - shows when AI is thinking and it's this player's view */}
            {humanPlayer &&
             humanPlayer.name === playerName &&
             !humanPlayer.is_folded &&
             aiThinking &&
             currentPlayer &&
             !currentPlayer.is_human && (
              <button
                className={`action-btn preemptive-btn ${queuedAction === 'check_fold' ? 'queued' : ''}`}
                data-testid="action-btn-preemptive"
                onClick={() => setQueuedAction(queuedAction === 'check_fold' ? null : 'check_fold')}
              >
                <span className="action-icon">{queuedAction === 'check_fold' ? <Check /> : <><Check /><X /></>}</span>
                <span className="btn-label">{queuedAction === 'check_fold' ? 'Queued' : 'Chk/Fold'}</span>
              </button>
            )}
            <span className="waiting-text" data-testid="waiting-text">
              {aiThinking && currentPlayer ? `${currentPlayer.name} is thinking...` : 'Waiting...'}
            </span>
            <button
              className="action-btn chat-btn"
              onClick={() => setShowChatSheet(true)}
            >
              <span className="action-icon"><MessageCircle /></span>
              <span className="btn-label">Chat</span>
            </button>
          </div>
        )}
      </div>

      {/* Winner Announcement */}
      <MobileWinnerAnnouncement
        winnerInfo={winnerInfo}
        onComplete={clearWinnerInfo}
        gameId={gameId || ''}
        playerName={playerName || ''}
        onSendMessage={wrappedSendMessage}
      />

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

      {/* Chat Sheet - Redesigned with tabs for Quick Chat / Keyboard */}
      <MobileChatSheet
        isOpen={showChatSheet}
        onClose={() => setShowChatSheet(false)}
        messages={messages}
        onSendMessage={wrappedSendMessage}
        gameId={providedGameId || ''}
        playerName={playerName || 'Player'}
        players={gameState?.players || []}
        guestChatDisabled={guestChatDisabled}
        isGuest={isGuest}
      />

      {/* LLM Debug Modal */}
      <LLMDebugModal
        isOpen={!!debugModalPlayer}
        onClose={() => setDebugModalPlayer(null)}
        playerName={debugModalPlayer?.name || ''}
        debugInfo={debugModalPlayer?.llm_debug}
      />

      {/* Coach Components */}
      {coachEnabled && (
        <>
          <CoachButton
            onClick={() => setShowCoachPanel(true)}
            hasNewInsight={(!!coach.proactiveTip || coach.hasUnreadReview) && !showCoachPanel}
          />

          <CoachBubble
            isVisible={coach.mode === 'proactive' && !!showActionButtons && !!coach.proactiveTip && !showCoachPanel}
            tip={coach.proactiveTip}
            stats={coach.stats}
            onTap={() => setShowCoachPanel(true)}
            onDismiss={coach.clearProactiveTip}
          />

          <CoachPanel
            isOpen={showCoachPanel}
            onClose={() => setShowCoachPanel(false)}
            stats={coach.stats}
            messages={coach.messages}
            onSendQuestion={coach.sendQuestion}
            isThinking={coach.isThinking}
            mode={coach.mode}
            onModeChange={coach.setMode}
          />
        </>
      )}

      {/* Guest Hand Limit Modal */}
      {guestLimitReached && usageStats && (
        <GuestLimitModal
          handsPlayed={usageStats.hands_played}
          handsLimit={usageStats.hands_limit}
          onReturnToMenu={() => { if (onBack) onBack(); }}
        />
      )}
    </div>
  );
}
