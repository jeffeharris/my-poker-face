import { useEffect, useState, useRef } from 'react';
import { io, Socket } from 'socket.io-client';
import type { ChatMessage, GameState } from '../../types';
import { Card } from '../cards';
import { MobileActionButtons } from './MobileActionButtons';
import { FloatingChat } from './FloatingChat';
import { MobileWinnerAnnouncement } from './MobileWinnerAnnouncement';
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
  const [gameState, setGameState] = useState<GameState | null>(null);
  const [loading, setLoading] = useState(true);
  const [gameId, setGameId] = useState<string | null>(null);
  const [aiThinking, setAiThinking] = useState(false);
  const socketRef = useRef<Socket | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const messageIdsRef = useRef<Set<string>>(new Set());
  const [winnerInfo, setWinnerInfo] = useState<any>(null);
  const [showChatSheet, setShowChatSheet] = useState(false);
  const [recentAiMessage, setRecentAiMessage] = useState<ChatMessage | null>(null);
  const chatMessagesRef = useRef<HTMLDivElement>(null);

  // Scroll chat to bottom only when first opened
  useEffect(() => {
    if (showChatSheet && chatMessagesRef.current) {
      // Use setTimeout to ensure DOM has rendered
      setTimeout(() => {
        if (chatMessagesRef.current) {
          chatMessagesRef.current.scrollTop = chatMessagesRef.current.scrollHeight;
        }
      }, 0);
    }
  }, [showChatSheet]);

  const fetchWithCredentials = (url: string, options: RequestInit = {}) => {
    return fetch(url, {
      ...options,
      credentials: 'include',
    });
  };

  const setupSocketListeners = (socket: Socket) => {
    socket.on('disconnect', () => {
      console.log('WebSocket disconnected');
    });

    socket.on('update_game_state', (data: { game_state: any }) => {
      const transformedState = {
        ...data.game_state,
        messages: data.game_state.messages || []
      };
      setGameState(transformedState);

      if (data.game_state.messages) {
        const newMessages = data.game_state.messages.filter((msg: ChatMessage) => {
          return !messageIdsRef.current.has(msg.id);
        });

        if (newMessages.length > 0) {
          newMessages.forEach((msg: ChatMessage) => messageIdsRef.current.add(msg.id));
          setMessages(prev => [...prev, ...newMessages]);

          // Show recent AI messages as floating bubbles
          const aiMessages = newMessages.filter((msg: ChatMessage) => msg.type === 'ai');
          if (aiMessages.length > 0) {
            setRecentAiMessage(aiMessages[aiMessages.length - 1]);
          }
        }
      }

      const currentPlayer = transformedState.players[transformedState.current_player_idx];
      setAiThinking(!currentPlayer.is_human && !currentPlayer.is_folded);
    });

    socket.on('new_messages', (data: { game_messages: any[] }) => {
      const newMessages = data.game_messages.filter((msg: any) => {
        return !messageIdsRef.current.has(msg.id || String(msg.timestamp));
      });

      if (newMessages.length > 0) {
        newMessages.forEach((msg: any) => {
          const msgId = msg.id || String(msg.timestamp);
          messageIdsRef.current.add(msgId);
        });
        setMessages(prev => [...prev, ...newMessages]);

        const aiMessages = newMessages.filter((msg: any) => msg.type === 'ai');
        if (aiMessages.length > 0) {
          setRecentAiMessage(aiMessages[aiMessages.length - 1]);
        }
      }
    });

    socket.on('player_turn_start', (data: { current_player_options: string[] }) => {
      setAiThinking(false);
      setGameState(prev => {
        if (!prev) return prev;
        return {
          ...prev,
          player_options: data.current_player_options
        };
      });
    });

    socket.on('winner_announcement', (data: any) => {
      setWinnerInfo(data);
    });
  };

  useEffect(() => {
    if (providedGameId) {
      const loadGameId = providedGameId;
      setGameId(loadGameId);

      const socket = io(config.SOCKET_URL);
      socketRef.current = socket;

      socket.on('connect', () => {
        socket.emit('join_game', loadGameId);
      });

      setupSocketListeners(socket);

      fetchWithCredentials(`${config.API_URL}/api/game-state/${loadGameId}`)
        .then(res => res.json())
        .then(data => {
          if (data.error || !data.players || data.players.length === 0) {
            throw new Error(data.message || 'Invalid game state');
          }

          setGameState(data);
          setLoading(false);

          if (data.messages) {
            setMessages(data.messages);
            data.messages.forEach((msg: ChatMessage) => messageIdsRef.current.add(msg.id));
          }

          const currentPlayer = data.players[data.current_player_idx];
          if (!currentPlayer.is_human) {
            setAiThinking(true);
          }
        })
        .catch(err => {
          console.error('Failed to load game:', err);
          localStorage.removeItem('pokerGameState');
          if (onGameCreated) onGameCreated('');
          window.location.reload();
        });
    } else {
      fetchWithCredentials(`${config.API_URL}/api/new-game`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ playerName: playerName || 'Player' }),
      })
        .then(res => res.json())
        .then(data => {
          const newGameId = data.game_id;
          setGameId(newGameId);

          if (onGameCreated) onGameCreated(newGameId);

          const socket = io(config.SOCKET_URL);
          socketRef.current = socket;

          socket.on('connect', () => {
            socket.emit('join_game', newGameId);
          });

          setupSocketListeners(socket);

          return fetchWithCredentials(`${config.API_URL}/api/game-state/${newGameId}`);
        })
        .then(res => res.json())
        .then(data => {
          setGameState(data);
          setLoading(false);

          if (data.messages) {
            setMessages(data.messages);
            data.messages.forEach((msg: ChatMessage) => messageIdsRef.current.add(msg.id));
          }

          const currentPlayer = data.players[data.current_player_idx];
          if (!currentPlayer.is_human) {
            setAiThinking(true);
          }
        })
        .catch(err => {
          console.error('Failed to create/fetch game:', err);
          setLoading(false);
        });
    }
  }, [providedGameId]);

  useEffect(() => {
    return () => {
      if (socketRef.current) {
        socketRef.current.disconnect();
      }
    };
  }, []);

  const handlePlayerAction = async (action: string, amount?: number) => {
    if (!gameId) return;

    setAiThinking(true);

    try {
      const response = await fetchWithCredentials(`${config.API_URL}/api/game/${gameId}/action`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action, amount: amount || 0 }),
      });

      if (!response.ok) {
        throw new Error('Action failed');
      }
    } catch (error) {
      console.error('Failed to send action:', error);
      setAiThinking(false);
    }
  };

  const handleSendMessage = async (message: string) => {
    if (!gameId) return;

    try {
      await fetchWithCredentials(`${config.API_URL}/api/game/${gameId}/message`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message, sender: playerName || 'Player' }),
      });
    } catch (error) {
      console.error('Failed to send message:', error);
    }
  };

  const currentPlayer = gameState?.players[gameState.current_player_idx];
  const humanPlayer = gameState?.players.find(p => p.is_human);
  const opponents = gameState?.players.filter(p => !p.is_human) || [];

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
              {isCurrentPlayer && aiThinking && (
                <div className="thinking-indicator">
                  <span className="dot">•</span>
                  <span className="dot">•</span>
                  <span className="dot">•</span>
                </div>
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
        onComplete={() => setWinnerInfo(null)}
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
