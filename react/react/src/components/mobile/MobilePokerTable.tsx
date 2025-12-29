import { useEffect, useState, useRef, useCallback } from 'react';
import type { ChatMessage } from '../../types';
import { Card } from '../cards';
import { MobileActionButtons } from './MobileActionButtons';
import { FloatingChat } from './FloatingChat';
import { MobileWinnerAnnouncement } from './MobileWinnerAnnouncement';
import { usePokerGame } from '../../hooks/usePokerGame';
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
  const [recentAiMessage, setRecentAiMessage] = useState<ChatMessage | null>(null);
  const chatMessagesRef = useRef<HTMLDivElement>(null);

  // Callback for handling AI messages (for floating bubbles)
  const handleNewAiMessage = useCallback((message: ChatMessage) => {
    setRecentAiMessage(message);
  }, []);

  // Use the shared hook for all socket/state management
  const {
    gameState,
    loading,
    messages,
    aiThinking,
    winnerInfo,
    handlePlayerAction,
    handleSendMessage,
    clearWinnerInfo,
  } = usePokerGame({
    gameId: providedGameId ?? null,
    playerName,
    onGameCreated,
    onNewAiMessage: handleNewAiMessage,
  });

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

  const showActionButtons = currentPlayer?.is_human &&
                           !currentPlayer.is_folded &&
                           gameState?.player_options &&
                           gameState.player_options.length > 0 &&
                           !aiThinking;

  if (loading) {
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

  return (
    <div className="mobile-poker-table">
      {/* Header with back button and pot */}
      <div className="mobile-header">
        <button className="mobile-back-btn" onClick={onBack}>
          <span>←</span>
        </button>
        <div className="mobile-pot">
          <span className="pot-label">POT</span>
          <span className="pot-amount">${gameState.pot.total}</span>
        </div>
        <button
          className="mobile-chat-toggle"
          onClick={() => setShowChatSheet(true)}
        >
          💬
          {messages.length > 0 && (
            <span className="chat-badge">{messages.length}</span>
          )}
        </button>
      </div>

      {/* Opponents Strip */}
      <div className="mobile-opponents">
        {opponents.map((opponent) => {
          const opponentIdx = gameState.players.findIndex(p => p.name === opponent.name);
          const isCurrentPlayer = opponentIdx === gameState.current_player_idx;
          const isDealer = opponentIdx === gameState.current_dealer_idx;

          return (
            <div
              key={opponent.name}
              className={`mobile-opponent ${opponent.is_folded ? 'folded' : ''} ${opponent.is_all_in ? 'all-in' : ''} ${isCurrentPlayer ? 'thinking' : ''}`}
            >
              <div className="opponent-avatar">
                {opponent.name.charAt(0).toUpperCase()}
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
      </div>

      {/* Community Cards */}
      <div className="mobile-community">
        <div className="community-cards-row">
          {gameState.community_cards.length > 0 ? (
            gameState.community_cards.map((card, i) => (
              <Card key={i} card={card} faceDown={false} size="medium" />
            ))
          ) : (
            <div className="waiting-for-flop">Waiting for flop...</div>
          )}
        </div>
        <div className="phase-indicator">{gameState.phase.replace('_', ' ')}</div>
      </div>

      {/* Floating AI Message */}
      <FloatingChat
        message={recentAiMessage}
        onDismiss={() => setRecentAiMessage(null)}
      />

      {/* Hero Section - Your Cards */}
      <div className="mobile-hero">
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
        <div className="hero-cards">
          {humanPlayer?.hand ? (
            <>
              <Card card={humanPlayer.hand[0]} faceDown={false} size="large" className="hero-card" />
              <Card card={humanPlayer.hand[1]} faceDown={false} size="large" className="hero-card" />
            </>
          ) : (
            <>
              <div className="card-placeholder" />
              <div className="card-placeholder" />
            </>
          )}
        </div>
      </div>

      {/* Action Buttons */}
      {showActionButtons && currentPlayer && (
        <MobileActionButtons
          playerOptions={gameState.player_options}
          currentPlayerStack={currentPlayer.stack}
          highestBet={gameState.highest_bet}
          currentPlayerBet={currentPlayer.bet}
          minRaise={gameState.min_raise}
          bigBlind={gameState.big_blind}
          potSize={gameState.pot.total}
          onAction={handlePlayerAction}
        />
      )}

      {/* AI Thinking Overlay - subtle for mobile */}
      {aiThinking && !showActionButtons && (
        <div className="mobile-waiting">
          <div className="waiting-text">
            {currentPlayer?.name} is thinking...
          </div>
        </div>
      )}

      {/* Winner Announcement */}
      <MobileWinnerAnnouncement
        winnerInfo={winnerInfo}
        onComplete={clearWinnerInfo}
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
