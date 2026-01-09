import { useEffect, useState, useRef, useCallback, useMemo } from 'react';
import type { ChatMessage } from '../../types';
import { Card } from '../cards';
import { MobileActionButtons } from './MobileActionButtons';
import { FloatingChat } from './FloatingChat';
import { MobileWinnerAnnouncement } from './MobileWinnerAnnouncement';
import { TournamentComplete } from '../game/TournamentComplete';
import { QuickChatSuggestions } from '../chat/QuickChatSuggestions';
import { HeadsUpOpponentPanel } from './HeadsUpOpponentPanel';
import { MobileHeader, PotDisplay, ChatToggle } from '../shared';
import { usePokerGame } from '../../hooks/usePokerGame';
import { config } from '../../config';
import './MobilePokerTable.css';

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
  const [showQuickChat, setShowQuickChat] = useState(false);
  const [recentAiMessage, setRecentAiMessage] = useState<ChatMessage | null>(null);
  const chatMessagesRef = useRef<HTMLDivElement>(null);
  const opponentsContainerRef = useRef<HTMLDivElement>(null);
  const opponentRefs = useRef<Map<string, HTMLDivElement>>(new Map());

  // Track if cards are in "neat" (straightened) position
  const [cardsNeat, setCardsNeat] = useState(false);

  // Callbacks for handling AI messages (for floating bubbles)
  const handleNewAiMessage = useCallback((message: ChatMessage) => {
    setRecentAiMessage(message);
  }, []);

  const dismissRecentAiMessage = useCallback(() => {
    setRecentAiMessage(null);
  }, []);

  // Debug mode state
  const [showDebugButtons, setShowDebugButtons] = useState(false);
  const [promptCaptureEnabled, setPromptCaptureEnabled] = useState(false);

  // Toggle prompt capture for debugging AI decisions
  const togglePromptCapture = async () => {
    if (!gameId) return;
    try {
      const newState = !promptCaptureEnabled;
      const response = await fetch(`${config.API_URL}/api/prompt-debug/game/${gameId}/debug-mode`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({ enabled: newState }),
      });
      if (response.ok) {
        setPromptCaptureEnabled(newState);
      }
    } catch (error) {
      console.error('Failed to toggle prompt capture:', error);
    }
  };

  // Use the shared hook for all socket/state management
  const {
    gameState,
    loading,
    gameId,
    messages,
    aiThinking,
    winnerInfo,
    tournamentResult,
    isConnected,
    queuedAction,
    setQueuedAction,
    handlePlayerAction,
    handleSendMessage,
    clearWinnerInfo,
    clearTournamentResult,
    debugTriggerTournamentEnd,
    debugTriggerSplitPot,
    debugTriggerSidePot,
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
    localStorage.removeItem('activePokerGameId');
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

  // Create stable card identifiers (only changes when actual cards change)
  const card1Id = humanPlayer?.hand?.[0] ? `${humanPlayer.hand[0].rank}-${humanPlayer.hand[0].suit}` : null;
  const card2Id = humanPlayer?.hand?.[1] ? `${humanPlayer.hand[1].rank}-${humanPlayer.hand[1].suit}` : null;

  // Track if cards are currently being dealt (for animation)
  const [isDealing, setIsDealing] = useState(false);
  const prevCard1Id = useRef<string | null>(null);

  // Reset neat state and trigger deal animation when hand changes
  useEffect(() => {
    if (card1Id && card1Id !== prevCard1Id.current) {
      setCardsNeat(false);
      setIsDealing(true);
      // Reset dealing state after animation completes
      const timer = setTimeout(() => setIsDealing(false), 600);
      prevCard1Id.current = card1Id;
      return () => clearTimeout(timer);
    }
    if (!card1Id) {
      prevCard1Id.current = null;
    }
  }, [card1Id]);

  // Auto-scroll to center the active opponent when turn changes
  useEffect(() => {
    if (!gameState || !currentPlayer || currentPlayer.is_human) return;

    const opponentEl = opponentRefs.current.get(currentPlayer.name);
    const containerEl = opponentsContainerRef.current;

    if (opponentEl && containerEl) {
      // Calculate scroll position to center the element
      const containerWidth = containerEl.offsetWidth;
      const elementLeft = opponentEl.offsetLeft;
      const elementWidth = opponentEl.offsetWidth;
      const scrollTarget = elementLeft - (containerWidth / 2) + (elementWidth / 2);

      containerEl.scrollTo({
        left: scrollTarget,
        behavior: 'smooth'
      });
    }
  }, [gameState?.current_player_idx, currentPlayer?.name]);

  // Random card transforms for natural "dealt" look
  // Card 1: -3° base ±7° range, Card 2: +3° base ±7° range
  // Y offset: ±8px, Gap: 10px base ±10px range
  const randomTransforms = useMemo(() => ({
    card1: {
      rotation: -3 + (Math.random() * 14 - 7),  // -10 to +4
      offsetY: Math.random() * 16 - 8,          // -8 to +8
    },
    card2: {
      rotation: 3 + (Math.random() * 14 - 7),   // -4 to +10
      offsetY: Math.random() * 16 - 8,          // -8 to +8
    },
    gap: 10 + (Math.random() * 20 - 10),        // 0 to 20
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }), [card1Id, card2Id]);

  // Use neat or random transforms based on state
  const neatTransforms = { card1: { rotation: 0, offsetY: 0 }, card2: { rotation: 0, offsetY: 0 }, gap: 12 };
  const cardTransforms = cardsNeat ? neatTransforms : randomTransforms;

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

      {/* Header with back button and pot */}
      <MobileHeader
        onBack={onBack}
        centerContent={<PotDisplay total={gameState.pot.total} />}
        rightContent={
          <ChatToggle
            onClick={() => setShowChatSheet(true)}
            badgeCount={messages.length}
          />
        }
      />

      {/* Debug buttons for testing tournament end */}
      {config.ENABLE_DEBUG && (
        <div style={{
          position: 'absolute',
          top: '50px',
          right: '10px',
          zIndex: 100,
          display: 'flex',
          flexDirection: 'column',
          gap: '8px',
        }}>
          {!showDebugButtons ? (
            <button
              onClick={() => setShowDebugButtons(true)}
              style={{
                padding: '6px 10px',
                fontSize: '12px',
                backgroundColor: '#666',
                color: '#fff',
                border: 'none',
                borderRadius: '4px',
              }}
            >
              🐛
            </button>
          ) : (
            <>
              <button
                onClick={() => setShowDebugButtons(false)}
                style={{
                  padding: '6px 10px',
                  fontSize: '12px',
                  backgroundColor: '#666',
                  color: '#fff',
                  border: 'none',
                  borderRadius: '4px',
                }}
              >
                ✕ Close
              </button>
              <button
                onClick={() => debugTriggerTournamentEnd(false)}
                style={{
                  padding: '8px 12px',
                  fontSize: '12px',
                  backgroundColor: '#e74c3c',
                  color: '#fff',
                  border: 'none',
                  borderRadius: '4px',
                }}
              >
                Test: Lost
              </button>
              <button
                onClick={() => debugTriggerTournamentEnd(true)}
                style={{
                  padding: '8px 12px',
                  fontSize: '12px',
                  backgroundColor: '#f1c40f',
                  color: '#000',
                  border: 'none',
                  borderRadius: '4px',
                }}
              >
                Test: Won
              </button>
              <button
                onClick={togglePromptCapture}
                style={{
                  padding: '8px 12px',
                  fontSize: '12px',
                  backgroundColor: promptCaptureEnabled ? '#e74c3c' : '#3498db',
                  color: '#fff',
                  border: 'none',
                  borderRadius: '4px',
                }}
              >
                {promptCaptureEnabled ? '🔴 Stop Capture' : '⏺️ Capture AI'}
              </button>
              <button
                onClick={() => debugTriggerSplitPot()}
                style={{
                  padding: '8px 12px',
                  fontSize: '12px',
                  backgroundColor: '#9b59b6',
                  color: '#fff',
                  border: 'none',
                  borderRadius: '4px',
                }}
              >
                Test: Split Pot
              </button>
              <button
                onClick={() => debugTriggerSidePot()}
                style={{
                  padding: '8px 12px',
                  fontSize: '12px',
                  backgroundColor: '#3498db',
                  color: '#fff',
                  border: 'none',
                  borderRadius: '4px',
                }}
              >
                Test: Side Pots
              </button>
            </>
          )}
        </div>
      )}

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
              <div className="opponent-avatar">
                {opponent.avatar_url ? (
                  <img
                    src={`${config.API_URL}${opponent.avatar_url}`}
                    alt={`${opponent.name} - ${opponent.avatar_emotion || 'avatar'}`}
                    className="avatar-image"
                  />
                ) : (
                  opponent.name.charAt(0).toUpperCase()
                )}
                {isDealer && <span className="dealer-badge">D</span>}
              </div>
              <div className="opponent-info">
                <span className="opponent-name">{opponent.name.split(' ')[0]}</span>
                <span className="opponent-stack">${opponent.stack}</span>
              </div>
              {opponent.bet > 0 && (
                <div className="opponent-bet">${opponent.bet}</div>
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
        <div className="phase-indicator">{gameState.phase.replace('_', ' ')}</div>
      </div>

      {/* Floating AI Message */}
      <FloatingChat
        message={recentAiMessage}
        onDismiss={dismissRecentAiMessage}
        players={gameState.players}
      />

      {/* Hero Section - Your Cards */}
      <div className={`mobile-hero ${currentPlayer?.is_human ? 'active-turn' : ''} ${humanPlayer?.is_folded ? 'folded' : ''}`}>
        <div className="hero-info">
          <div className="hero-name">
            {humanPlayer?.name}
            {gameState.players.findIndex(p => p.is_human) === gameState.current_dealer_idx && (
              <span className="dealer-chip">D</span>
            )}
          </div>
          <div className="hero-stack">${humanPlayer?.stack}</div>
          {humanPlayer?.bet && humanPlayer.bet > 0 && (
            <div className="hero-bet">Bet: ${humanPlayer.bet}</div>
          )}
        </div>
        <div className="hero-cards" style={{ gap: `${cardTransforms.gap}px`, transition: cardsNeat ? 'gap 0.2s ease-out' : 'none' }}>
          {humanPlayer?.hand ? (
            <>
              <div
                onClick={() => setCardsNeat(n => !n)}
                style={{
                  transform: `rotate(${cardTransforms.card1.rotation}deg) translateY(${cardTransforms.card1.offsetY}px)`,
                  transition: cardsNeat ? 'transform 0.2s ease-out' : 'none',
                  cursor: 'pointer',
                  animation: isDealing ? `dealCardIn 0.3s ease-out forwards` : 'none',
                  '--deal-rotation': `${cardTransforms.card1.rotation}deg`,
                  '--deal-offset': `${cardTransforms.card1.offsetY}px`,
                } as React.CSSProperties}
              >
                <Card card={humanPlayer.hand[0]} faceDown={false} size="large" className="hero-card" />
              </div>
              <div
                onClick={() => setCardsNeat(n => !n)}
                style={{
                  transform: `rotate(${cardTransforms.card2.rotation}deg) translateY(${cardTransforms.card2.offsetY}px)`,
                  transition: cardsNeat ? 'transform 0.2s ease-out' : 'none',
                  cursor: 'pointer',
                  animation: isDealing ? `dealCardIn 0.3s ease-out 0.15s forwards` : 'none',
                  opacity: isDealing ? 0 : 1,
                  '--deal-rotation': `${cardTransforms.card2.rotation}deg`,
                  '--deal-offset': `${cardTransforms.card2.offsetY}px`,
                } as React.CSSProperties}
              >
                <Card card={humanPlayer.hand[1]} faceDown={false} size="large" className="hero-card" />
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
            onQuickChat={() => setShowQuickChat(true)}
          />
        ) : (
          <div className="mobile-action-buttons">
            {/* Preemptive Check/Fold - shows when AI is thinking */}
            {humanPlayer && !humanPlayer.is_folded && aiThinking && currentPlayer && !currentPlayer.is_human && (
              <button
                className={`action-btn preemptive-btn ${queuedAction === 'check_fold' ? 'queued' : ''}`}
                onClick={() => setQueuedAction(queuedAction === 'check_fold' ? null : 'check_fold')}
              >
                <span className="btn-icon">{queuedAction === 'check_fold' ? '✓' : '✓✕'}</span>
                <span className="btn-label">{queuedAction === 'check_fold' ? 'Queued' : 'Chk/Fold'}</span>
              </button>
            )}
            <span className="waiting-text">
              {aiThinking && currentPlayer ? `${currentPlayer.name} is thinking...` : 'Waiting...'}
            </span>
            <button
              className="action-btn chat-btn"
              onClick={() => setShowQuickChat(true)}
            >
              <span className="btn-icon">💬</span>
              <span className="btn-label">Chat</span>
            </button>
          </div>
        )}
      </div>

      {/* Quick Chat Overlay */}
      {showQuickChat && providedGameId && gameState?.players && (
        <div className="quick-chat-overlay" onClick={() => setShowQuickChat(false)}>
          <div className="quick-chat-modal" onClick={e => e.stopPropagation()}>
            <div className="quick-chat-modal-header">
              <button onClick={() => setShowQuickChat(false)}>Cancel</button>
              <span className="header-title">Quick Chat</span>
              <button style={{ visibility: 'hidden' }}>Cancel</button>
            </div>
            <QuickChatSuggestions
              gameId={providedGameId}
              playerName={playerName || 'Player'}
              players={gameState.players}
              defaultExpanded={true}
              hideHeader={true}
              onSelectSuggestion={(text) => {
                handleSendMessage(text);
                setShowQuickChat(false);
              }}
            />
          </div>
        </div>
      )}


      {/* Winner Announcement */}
      <MobileWinnerAnnouncement
        winnerInfo={winnerInfo}
        onComplete={clearWinnerInfo}
        gameId={gameId || ''}
        playerName={playerName || ''}
        onSendMessage={handleSendMessage}
      />

      {/* Tournament Complete */}
      <TournamentComplete
        result={tournamentResult}
        onComplete={handleTournamentComplete}
      />

      {/* Chat Sheet (bottom drawer) */}
      {showChatSheet && (
        <div className="chat-sheet-overlay" onClick={() => setShowChatSheet(false)}>
          <div className="chat-sheet" onClick={e => e.stopPropagation()}>
            <div className="chat-sheet-header">
              <h3>Table Chat</h3>
              <button onClick={() => setShowChatSheet(false)}>×</button>
            </div>
            <div className="chat-sheet-messages" ref={chatMessagesRef}>
              {messages.slice(-50).map((msg, i) => (
                <div key={msg.id || i} className={`chat-msg ${msg.type}`}>
                  <span className="chat-sender">{msg.sender}:</span>
                  <span className="chat-text">{msg.message}</span>
                </div>
              ))}
            </div>
            {providedGameId && gameState?.players && (
              <QuickChatSuggestions
                gameId={providedGameId}
                playerName={playerName || 'Player'}
                players={gameState.players}
                onSelectSuggestion={(text) => {
                  handleSendMessage(text);
                }}
              />
            )}
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
    </div>
  );
}
