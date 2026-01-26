import { useEffect, useState, useRef, useCallback } from 'react';
import { Check, X, MessageCircle } from 'lucide-react';
import type { ChatMessage } from '../../types';
import type { Player } from '../../types/player';
import { Card } from '../cards';
import { MobileActionButtons } from './MobileActionButtons';
import { FloatingChat } from './FloatingChat';
import { MobileWinnerAnnouncement } from './MobileWinnerAnnouncement';
import { TournamentComplete } from '../game/TournamentComplete';
import { QuickChatSuggestions } from '../chat/QuickChatSuggestions';
import { HeadsUpOpponentPanel } from './HeadsUpOpponentPanel';
import { LLMDebugModal } from './LLMDebugModal';
import { MenuBar, PotDisplay, GameInfoDisplay } from '../shared';
import { usePokerGame } from '../../hooks/usePokerGame';
import { useCardAnimation } from '../../hooks/useCardAnimation';
import { config } from '../../config';
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
  const chatMessagesRef = useRef<HTMLDivElement>(null);
  const opponentsContainerRef = useRef<HTMLDivElement>(null);
  const opponentRefs = useRef<Map<string, HTMLDivElement>>(new Map());

  // Callbacks for handling AI messages (for floating bubbles)
  const handleNewAiMessage = useCallback((message: ChatMessage) => {
    setRecentAiMessage(message);
  }, []);

  const dismissRecentAiMessage = useCallback(() => {
    setRecentAiMessage(null);
  }, []);

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
    queuedAction,
    setQueuedAction,
    handlePlayerAction,
    handleSendMessage,
    clearWinnerInfo,
    clearTournamentResult,
  } = usePokerGame({
    gameId: providedGameId ?? null,
    playerName,
    onGameCreated,
    onNewAiMessage: handleNewAiMessage,
  });

  // Handle tournament completion - clean up and return to menu
  const handleTournamentComplete = useCallback(async () => {
    if (gameId) {
      try {
        await fetch(`${config.API_URL}/api/end_game/${gameId}`, {
          method: 'POST',
          credentials: 'include',
        });
      } catch (err) {
        console.error(`Failed to end game for gameId=${gameId}:`, err);
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

  // Scroll chat to bottom only when first opened
  useEffect(() => {
    if (showChatSheet && chatMessagesRef.current) {
      setTimeout(() => {
        if (chatMessagesRef.current) {
          chatMessagesRef.current.scrollTop = chatMessagesRef.current.scrollHeight;
        }
      }, 0);
    }
  }, [showChatSheet]);

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

  const showActionButtons = currentPlayer?.is_human &&
                           !currentPlayer.is_folded &&
                           gameState?.player_options &&
                           gameState.player_options.length > 0 &&
                           !aiThinking;

  // Only show full loading screen on initial load (no game state yet)
  // If we have game state but are disconnected, we'll show a reconnecting overlay instead
  if (loading && !gameState) {
    return (
      <div className="mobile-poker-table mobile-loading">
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
    <div className="mobile-poker-table">
      {/* Reconnecting overlay - shows when socket is disconnected but we have game state */}
      {showReconnecting && (
        <div className="mobile-reconnecting-overlay">
          <div className="mobile-reconnecting-indicator">
            <div className="reconnecting-spinner"></div>
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
          />
        }
        showUserInfo
        onAdminTools={() => { window.location.href = '/admin'; }}
      />
      {/* Spacer for fixed MenuBar */}
      <div className="menu-bar-spacer" />

      {/* Opponents Strip */}
      <div className={`mobile-opponents ${isHeadsUp ? 'heads-up-mode' : ''}`} ref={opponentsContainerRef}>
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
              className={`mobile-opponent ${opponent.is_folded ? 'folded' : ''} ${opponent.is_all_in ? 'all-in' : ''} ${isCurrentPlayer ? 'thinking' : ''} ${isHeadsUp ? 'heads-up-avatar' : ''}`}
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
                <span className="opponent-name">{opponent.name.split(' ')[0]}</span>
                <span className="opponent-stack">${opponent.stack}</span>
              </div>
              {opponent.bet > 0 && (
                <div className="opponent-bet">${opponent.bet}</div>
              )}
              {/* Revealed hole cards during run-it-out showdown */}
              {revealedCards?.players_cards[opponent.name] && (
                <div className="opponent-revealed-cards">
                  {revealedCards.players_cards[opponent.name].map((card, i) => (
                    <Card key={i} card={card} faceDown={false} size="small" />
                  ))}
                </div>
              )}
              {opponent.is_folded && <div className="status-badge folded">FOLD</div>}
              {opponent.is_all_in && <div className="status-badge all-in">ALL-IN</div>}
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
      <div className="mobile-floating-pot">
        <PotDisplay total={gameState.pot.total} />
      </div>

      {/* Community Cards - Always show 5 slots */}
      <div className="mobile-community">
        <div className="community-cards-row">
          {/* Show dealt cards */}
          {gameState.community_cards.map((card, i) => (
            <Card key={i} card={card} faceDown={false} size="medium" />
          ))}
          {/* Show placeholders for remaining cards */}
          {Array.from({ length: 5 - gameState.community_cards.length }).map((_, i) => (
            <div key={`placeholder-${i}`} className="community-card-placeholder" />
          ))}
        </div>
      </div>

      {/* Floating AI Message */}
      <FloatingChat
        message={recentAiMessage}
        onDismiss={dismissRecentAiMessage}
        players={gameState.players}
      />

      {/* Hero Section - Your Cards */}
      <div className={`mobile-hero ${currentPlayer?.is_human ? 'active-turn' : ''} ${humanPlayer?.is_folded ? 'folded' : ''}`}>
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
        <div className="hero-cards" style={{ gap: `${cardTransforms.gap}px`, transition: cardsNeat ? 'gap 0.2s ease-out' : 'none' }}>
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
                <Card card={displayCards[0]} faceDown={false} size="large" className="hero-card" />
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
                <Card card={displayCards[1]} faceDown={false} size="large" className="hero-card" />
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
                <Card card={displayCards[0]} faceDown={false} size="large" className="hero-card" />
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
                <Card card={displayCards[1]} faceDown={false} size="large" className="hero-card" />
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
                onClick={() => setQueuedAction(queuedAction === 'check_fold' ? null : 'check_fold')}
              >
                <span className="action-icon">{queuedAction === 'check_fold' ? <Check /> : <><Check /><X /></>}</span>
                <span className="btn-label">{queuedAction === 'check_fold' ? 'Queued' : 'Chk/Fold'}</span>
              </button>
            )}
            <span className="waiting-text">
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
        onSendMessage={handleSendMessage}
      />

      {/* Tournament Complete - only show when winner announcement is dismissed */}
      {/* This ensures winner announcement is ALWAYS shown first, then tournament complete after */}
      {!winnerInfo && (
        <TournamentComplete
          result={tournamentResult}
          onComplete={handleTournamentComplete}
          gameId={gameId || undefined}
          playerName={playerName}
          onSendMessage={handleSendMessage}
        />
      )}

      {/* Chat Sheet (bottom drawer) - Quick Chat prioritized at top */}
      {showChatSheet && (
        <div className="chat-sheet-overlay" onClick={() => setShowChatSheet(false)}>
          <div className="chat-sheet" onClick={e => e.stopPropagation()}>
            <div className="chat-sheet-header">
              <h3>Table Chat</h3>
              <button onClick={() => setShowChatSheet(false)}><X size={20} /></button>
            </div>
            {/* Quick Chat at top - expanded by default */}
            {providedGameId && gameState?.players && (
              <QuickChatSuggestions
                gameId={providedGameId}
                playerName={playerName || 'Player'}
                players={gameState.players}
                defaultExpanded={true}
                onSelectSuggestion={(text) => {
                  handleSendMessage(text);
                }}
              />
            )}
            {/* Message history below */}
            <div className="chat-sheet-messages" ref={chatMessagesRef}>
              {messages.slice(-50).map((msg, i) => (
                <div key={msg.id || i} className={`chat-msg ${msg.type}`}>
                  <span className="chat-sender">{msg.sender}:</span>
                  <span className="chat-text">{msg.message}</span>
                </div>
              ))}
            </div>
            <form
              className="chat-sheet-input"
              onSubmit={(e) => {
                e.preventDefault();
                const input = e.currentTarget.querySelector('input');
                if (input?.value.trim()) {
                  handleSendMessage(input.value.trim());
                  input.value = '';
                }
              }}
            >
              <input type="text" placeholder="Say something..." />
              <button type="submit">Send</button>
            </form>
          </div>
        </div>
      )}

      {/* LLM Debug Modal */}
      <LLMDebugModal
        isOpen={!!debugModalPlayer}
        onClose={() => setDebugModalPlayer(null)}
        playerName={debugModalPlayer?.name || ''}
        debugInfo={debugModalPlayer?.llm_debug}
      />
    </div>
  );
}
