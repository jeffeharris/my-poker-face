import { useEffect, useState, useRef } from 'react';
import { io, Socket } from 'socket.io-client';
import type { ChatMessage } from '../../../types';
import { Card, CommunityCard, HoleCard } from '../../cards';
import { ActionButtons } from '../ActionButtons';
import { Chat } from '../../chat/Chat';
import { ChatSidebar } from '../../chat/ChatSidebar';
import { LoadingIndicator } from '../LoadingIndicator';
import { PlayerThinking } from '../PlayerThinking';
import { WinnerAnnouncement } from '../WinnerAnnouncement';
import { ElasticityDebugPanel } from '../../debug/ElasticityDebugPanel';
import { PressureStats } from '../../stats';
import { PokerTableLayout } from '../PokerTableLayout';
import { DebugPanel } from '../../debug/DebugPanel';
import { config } from '../../../config';
import './PokerTable.css';

interface Player {
  name: string;
  stack: number;
  bet: number;
  is_folded: boolean;
  is_all_in: boolean;
  is_human: boolean;
  hand?: string[];
}

interface GameState {
  players: Player[];
  community_cards: string[];
  pot: { total: number };
  current_player_idx: number;
  current_dealer_idx: number;
  small_blind_idx: number;
  big_blind_idx: number;
  phase: string;
  highest_bet: number;
  player_options: string[];
  min_raise: number;
  big_blind: number;
  messages: ChatMessage[];
}

interface PokerTableProps {
  gameId?: string | null;
  playerName?: string;
  onGameCreated?: (gameId: string) => void;
}

export function PokerTable({ gameId: providedGameId, playerName, onGameCreated }: PokerTableProps) {
  const [gameState, setGameState] = useState<GameState | null>(null);
  const [loading, setLoading] = useState(true);

  // Helper function to ensure all fetch calls include credentials
  const fetchWithCredentials = (url: string, options: RequestInit = {}) => {
    return fetch(url, {
      ...options,
      credentials: 'include',
    });
  };
  const [gameId, setGameId] = useState<string | null>(null);
  const [aiThinking, setAiThinking] = useState(false);
  const [useOverlayLoading, setUseOverlayLoading] = useState(false); // Toggle between loading styles
  const [pollIntervalRef, setPollIntervalRef] = useState<NodeJS.Timeout | null>(null);
  const socketRef = useRef<Socket | null>(null);
  const [lastUpdate, setLastUpdate] = useState<Date | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const messageIdsRef = useRef<Set<string>>(new Set());
  const [winnerInfo, setWinnerInfo] = useState<any>(null);
  const [playerPositions, setPlayerPositions] = useState<Map<string, number>>(new Map());
  const [debugMode, setDebugMode] = useState<boolean>(config.ENABLE_DEBUG);
  const [showStats, setShowStats] = useState<boolean>(true); // Default to showing stats
  const [showElasticityPanel, setShowElasticityPanel] = useState<boolean>(false); // For comparison

  // Extract socket setup to avoid duplication
  const setupSocketListeners = (socket: Socket) => {
    socket.on('disconnect', () => {
      console.log('WebSocket disconnected');
    });
    
    socket.on('player_joined', (data: { message: string }) => {
      console.log('Player joined:', data.message);
    });
    
    // Listen for game state updates
    socket.on('update_game_state', (data: { game_state: any }) => {
      console.log('Received game state update via WebSocket');
      // Transform the game state data to match our interface
      const transformedState = {
        ...data.game_state,
        messages: data.game_state.messages || []
      };
      setGameState(transformedState);
      setLastUpdate(new Date());
      
      // Update messages more intelligently - only add new ones
      if (data.game_state.messages) {
        const newMessages = data.game_state.messages.filter((msg: ChatMessage) => {
          return !messageIdsRef.current.has(msg.id);
        });
        
        if (newMessages.length > 0) {
          newMessages.forEach((msg: ChatMessage) => messageIdsRef.current.add(msg.id));
          setMessages(prev => [...prev, ...newMessages]);
        }
      }
      
      // Update AI thinking state based on current player
      const currentPlayer = transformedState.players[transformedState.current_player_idx];
      setAiThinking(!currentPlayer.is_human && !currentPlayer.is_folded);
    });
    
    socket.on('new_messages', (data: { game_messages: any[] }) => {
      console.log('Received new messages via WebSocket');
      // Only add messages that we haven't seen before
      const newMessages = data.game_messages.filter((msg: any) => {
        return !messageIdsRef.current.has(msg.id || String(msg.timestamp));
      });
      
      if (newMessages.length > 0) {
        newMessages.forEach((msg: any) => {
          const msgId = msg.id || String(msg.timestamp);
          messageIdsRef.current.add(msgId);
        });
        setMessages(prev => [...prev, ...newMessages]);
      }
    });
    
    socket.on('player_turn_start', (data: { current_player_options: string[], cost_to_call: number }) => {
      console.log('Player turn started, options:', data.current_player_options);
      setAiThinking(false);
      // Update game state with the player options
      setGameState(prev => {
        if (!prev) return prev;
        return {
          ...prev,
          player_options: data.current_player_options
        };
      });
    });
    
    socket.on('winner_announcement', (data: any) => {
      console.log('Winner announcement received:', data);
      setWinnerInfo(data);
    });
  };

  useEffect(() => {
    // If a gameId is provided, load that game; otherwise create a new one
    if (providedGameId) {
      // Load existing game
      const loadGameId = providedGameId;
      setGameId(loadGameId);
      
      // Initialize WebSocket connection
      const socket = io(config.SOCKET_URL);
      socketRef.current = socket;
      
      socket.on('connect', () => {
        console.log('WebSocket connected');
        socket.emit('join_game', loadGameId);
        console.log('Joined existing game room:', loadGameId);
      });
      
      setupSocketListeners(socket);
      
      // Fetch the game state
      fetchWithCredentials(`${config.API_URL}/api/game-state/${loadGameId}`)
        .then(res => {
          if (!res.ok) {
            throw new Error('Failed to load game');
          }
          return res.json();
        })
        .then(data => {
          // Check if it's an error response
          if (data.error || !data.players || data.players.length === 0) {
            if (data.message) {
              throw new Error(data.message);
            } else {
              throw new Error('Invalid game state');
            }
          }
          
          setGameState(data);
          setLoading(false);
          
          // Only initialize positions if they haven't been set yet
          // This prevents positions from changing when dealer rotates
          if (playerPositions.size === 0) {
            const positions = new Map<string, number>();
            let humanIndex = data.players.findIndex((p: Player) => p.is_human);
            let positionIndex = 0;
            
            // Assign human player to position 0 (bottom)
            if (humanIndex !== -1) {
              positions.set(data.players[humanIndex].name, 0);
              positionIndex = 1;
            }
            
            // Assign other players to remaining positions
            data.players.forEach((player: Player, index: number) => {
              if (!player.is_human) {
                positions.set(player.name, positionIndex);
                positionIndex++;
              }
            });
            setPlayerPositions(positions);
          }
          
          // Initialize messages
          if (data.messages) {
            setMessages(data.messages);
            data.messages.forEach((msg: ChatMessage) => messageIdsRef.current.add(msg.id));
          }
          
          // Check if AI needs to act
          const currentPlayer = data.players[data.current_player_idx];
          if (!currentPlayer.is_human) {
            setAiThinking(true);
          }
        })
        .catch(err => {
          console.error('Failed to load game:', err);
          // Clear the bad game state from localStorage
          localStorage.removeItem('pokerGameState');
          // If we have an onGameCreated callback, notify parent to reset
          if (onGameCreated) {
            onGameCreated('');
          }
          // Reload the page to reset the app state
          window.location.reload();
        });
    } else {
      // Create a new game
      fetchWithCredentials(`${config.API_URL}/api/new-game`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          playerName: playerName || 'Player'
        }),
      })
        .then(res => res.json())
        .then(data => {
          const newGameId = data.game_id;
          setGameId(newGameId);
          
          // Notify parent component of the new game ID
          if (onGameCreated) {
            onGameCreated(newGameId);
          }
        
        // Initialize WebSocket connection
        const socket = io(config.SOCKET_URL);
        socketRef.current = socket;
        
        socket.on('connect', () => {
          console.log('WebSocket connected');
          socket.emit('join_game', newGameId);
          console.log('Joined new game room:', newGameId);
        });
        
        setupSocketListeners(socket);
        
        // Now fetch the initial game state
        return fetchWithCredentials(`${config.API_URL}/api/game-state/${newGameId}`);
      })
      .then(res => res.json())
      .then(data => {
        setGameState(data);
        setLoading(false);
        
        // Only initialize positions if they haven't been set yet
        // This prevents positions from changing when dealer rotates
        if (playerPositions.size === 0) {
          const positions = new Map<string, number>();
          let humanIndex = data.players.findIndex((p: Player) => p.is_human);
          let positionIndex = 0;
          
          // Assign human player to position 0 (bottom)
          if (humanIndex !== -1) {
            positions.set(data.players[humanIndex].name, 0);
            positionIndex = 1;
          }
          
          // Assign other players to remaining positions
          data.players.forEach((player: Player, index: number) => {
            if (!player.is_human) {
              positions.set(player.name, positionIndex);
              positionIndex++;
            }
          });
          setPlayerPositions(positions);
        }
        
        // Initialize messages
        if (data.messages) {
          setMessages(data.messages);
          data.messages.forEach((msg: ChatMessage) => messageIdsRef.current.add(msg.id));
        }
        
        // Check if AI needs to act
        const currentPlayer = data.players[data.current_player_idx];
        if (!currentPlayer.is_human) {
          setAiThinking(true);
          // No need to poll - WebSocket will handle updates
        }
      })
      .catch(err => {
        console.error('Failed to create/fetch game:', err);
        setLoading(false);
      });
    }
  }, [providedGameId]);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      if (pollIntervalRef) {
        clearInterval(pollIntervalRef);
      }
      if (socketRef.current) {
        console.log('Disconnecting WebSocket');
        socketRef.current.disconnect();
      }
    };
  }, []);

  // Polling function
  const startPolling = (gId: string) => {
    // Clear any existing interval
    if (pollIntervalRef) {
      clearInterval(pollIntervalRef);
    }
    
    const pollInterval = setInterval(async () => {
      try {
        console.log('Polling for updates...');
        const gameResponse = await fetchWithCredentials(`${config.API_URL}/api/game-state/${gId}`);
        const data = await gameResponse.json();
        setGameState(data);
        
        // Check if it's human's turn or game is over
        const currentPlayer = data.players[data.current_player_idx];
        console.log('Current player:', currentPlayer.name, 'Is human:', currentPlayer.is_human);
        
        if (currentPlayer.is_human || data.phase === 'GAME_OVER') {
          console.log('Stopping polling - human turn or game over');
          setAiThinking(false);
          clearInterval(pollInterval);
          setPollIntervalRef(null);
        } else {
          // AI is still thinking
          console.log('AI still thinking...');
          setAiThinking(true);
        }
      } catch (error) {
        console.error('Polling error:', error);
      }
    }, 1000); // Poll every 1 second for more responsive updates
    
    setPollIntervalRef(pollInterval);
  };

  const handlePlayerAction = async (action: string, amount?: number) => {
    if (!gameId) return;
    
    setAiThinking(true);
    
    try {
      const response = await fetchWithCredentials(`${config.API_URL}/api/game/${gameId}/action`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          action,
          amount: amount || 0
        }),
      });
      
      if (response.ok) {
        // WebSocket will handle updates, no need to poll
        console.log('Action sent successfully, waiting for WebSocket updates');
      }
    } catch (error) {
      console.error('Failed to send action:', error);
      alert('Failed to send action. Please try again.');
      setAiThinking(false);
    }
  };

  const handleSendMessage = async (message: string) => {
    if (!gameId) return;
    
    try {
      const response = await fetchWithCredentials(`${config.API_URL}/api/game/${gameId}/message`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          message,
          sender: playerName || 'Player'
        }),
      });
      
      if (response.ok) {
        // Refresh game state to get updated messages
        const gameResponse = await fetchWithCredentials(`${config.API_URL}/api/game-state/${gameId}`);
        const data = await gameResponse.json();
        setGameState(data);
      }
    } catch (error) {
      console.error('Failed to send message:', error);
    }
  };

  // Check if current player is human and it's their turn
  const currentPlayer = gameState?.players[gameState.current_player_idx];
  const showActionButtons = currentPlayer?.is_human && 
                           !currentPlayer.is_folded && 
                           gameState?.player_options && 
                           gameState.player_options.length > 0 &&
                           !aiThinking;
  
  // Debug logging
  if (currentPlayer?.is_human) {
    console.log('Human player turn check:', {
      is_human: currentPlayer.is_human,
      is_folded: currentPlayer.is_folded,
      player_options: gameState?.player_options,
      aiThinking,
      showActionButtons
    });
  }

  if (loading) {
    return (
      <div className="poker-table">
        <div className="initial-loading">
          <div className="loading-card-fan">
            {['♠', '♥', '♦', '♣'].map((suit, i) => (
              <div key={i} className={`loading-card card-${i}`}>
                <span className="suit">{suit}</span>
              </div>
            ))}
          </div>
          <h2>Setting up the table...</h2>
          <p>Shuffling cards and gathering players</p>
        </div>
      </div>
    );
  }
  
  if (!gameState) return <div className="error">No game state available</div>;

  return (
    <>
      {/* Control buttons - bottom left */}
      <div style={{
        position: 'fixed',
        bottom: '10px',
        left: '10px',
        zIndex: 1001,
        display: 'flex',
        flexDirection: 'column',
        gap: '8px'
      }}>
        {/* Stats toggle button */}
        <button
          className="stats-toggle"
          onClick={() => setShowStats(!showStats)}
          style={{
            padding: '8px 16px',
            backgroundColor: showStats ? '#ff9800' : '#666',
            color: '#fff',
            border: 'none',
            borderRadius: '4px',
            cursor: 'pointer',
            fontWeight: 'bold',
            boxShadow: '0 2px 4px rgba(0,0,0,0.2)',
            transition: 'all 0.3s ease'
          }}
        >
          {showStats ? '📊 Hide Stats' : '📊 Show Stats'}
        </button>
        
        {/* Show Debug button (original elasticity panel) */}
        <button
          onClick={() => setShowElasticityPanel(!showElasticityPanel)}
          style={{
            padding: '8px 16px',
            backgroundColor: showElasticityPanel ? '#4caf50' : '#666',
            color: '#fff',
            border: 'none',
            borderRadius: '4px',
            cursor: 'pointer',
            fontWeight: 'bold',
            boxShadow: '0 2px 4px rgba(0,0,0,0.2)',
            transition: 'all 0.3s ease'
          }}
        >
          {showElasticityPanel ? 'Hide Debug' : 'Show Debug'}
        </button>
        
        {/* Debug toggle button - only show if debug is enabled in config */}
        {config.ENABLE_DEBUG && (
          <button
            className="debug-toggle"
            onClick={() => setDebugMode(!debugMode)}
            style={{
              padding: '8px 16px',
              backgroundColor: debugMode ? '#4caf50' : '#666',
              color: '#fff',
              border: 'none',
              borderRadius: '4px',
              cursor: 'pointer',
              fontWeight: 'bold',
              boxShadow: '0 2px 4px rgba(0,0,0,0.2)',
              transition: 'all 0.3s ease'
            }}
          >
            {debugMode ? '🐛 Hide Debug' : '🐛 Show Debug'}
          </button>
        )}
      </div>
      
      
      {/* Pressure Stats Panel - positioned as overlay */}
      <PressureStats gameId={gameId} isOpen={showStats} socket={socketRef.current} />
      
      <PokerTableLayout
        chatPanel={
          <ChatSidebar 
            messages={messages}
            onSendMessage={handleSendMessage}
            playerName={playerName}
            gameId={gameId}
            isPlayerTurn={gameState && gameState.players[gameState.current_player_idx]?.name === playerName}
          />
        }
        debugPanel={
          <DebugPanel 
            gameId={gameId}
            socket={socketRef.current}
          />
        }
        actionButtons={
          showActionButtons && (
            <ActionButtons
              playerOptions={gameState.player_options}
              currentPlayerStack={currentPlayer.stack}
              highestBet={gameState.highest_bet}
              currentPlayerBet={currentPlayer.bet}
              minRaise={gameState.min_raise}
              bigBlind={gameState.big_blind}
              potSize={gameState.pot.total}
              onAction={handlePlayerAction}
            />
          )
        }
        showDebug={debugMode}
      >
        <div className="poker-table">
      <div className="table-felt">
        {/* Community Cards Area */}
        <div className="community-area">
          <div className="community-cards">
            {/* Show revealed community cards */}
            {gameState.community_cards.map((card, i) => (
              <CommunityCard key={i} card={card} revealed={true} />
            ))}
            {/* Show placeholder cards for remaining community cards */}
            {Array.from({ length: 5 - gameState.community_cards.length }).map((_, i) => (
              <CommunityCard key={`placeholder-${i}`} revealed={false} />
            ))}
          </div>
          
          <div className="pot-area">
            <div className="pot">
              <div className="pot-label">POT</div>
              <div className="pot-amount">${gameState.pot.total}</div>
            </div>
          </div>
        </div>

        {/* Player seats (moved further from table) */}
        <div className="players-area">
          {/* Sort players by their fixed positions */}
          {[...gameState.players]
            .sort((a, b) => {
              const posA = playerPositions.get(a.name) ?? 0;
              const posB = playerPositions.get(b.name) ?? 0;
              return posA - posB;
            })
            .map((player) => {
              // Get the player's current index in the original array for game logic
              const currentIndex = gameState.players.findIndex(p => p.name === player.name);
              // Get the player's fixed visual position
              const visualPosition = playerPositions.get(player.name) ?? 0;
              
              // Use current index for game logic checks
              const isDealer = currentIndex === gameState.current_dealer_idx;
              const isSmallBlind = currentIndex === gameState.small_blind_idx;
              const isBigBlind = currentIndex === gameState.big_blind_idx;
              const isCurrentPlayer = currentIndex === gameState.current_player_idx;
              
              return (
                <div 
                  key={player.name} 
                  className={`player-seat seat-${visualPosition} ${
                    isCurrentPlayer ? 'current-player' : ''
                  } ${player.is_folded ? 'folded' : ''} ${player.is_all_in ? 'all-in' : ''} ${
                    isCurrentPlayer && !player.is_human && aiThinking ? 'thinking' : ''
                  }`}
                >
                {/* Position indicators */}
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
                  <div className="player-name">{player.name}</div>
                  <div className="player-stack">${player.stack}</div>
                  {player.is_folded && <div className="status">FOLDED</div>}
                  {player.is_all_in && <div className="status">ALL-IN</div>}
                </div>
                
                {/* Player cards */}
                <div className="player-cards">
                  {player.is_human && player.hand ? (
                    // Show actual cards for human player (large size)
                    <>
                      <Card card={player.hand[0]} faceDown={false} size="large" className="hole-card" />
                      <Card card={player.hand[1]} faceDown={false} size="large" className="hole-card" />
                    </>
                  ) : (
                    // Show face-down cards for AI players (small size)
                    <>
                      <HoleCard visible={false} />
                      <HoleCard visible={false} />
                    </>
                  )}
                </div>
                
                {/* Show thinking indicator for current AI player */}
                {isCurrentPlayer && !player.is_human && aiThinking && (
                  <PlayerThinking playerName={player.name} position={visualPosition} />
                )}
              </div>
              );
            })}
        </div>

        {/* Bet chips on the table (separate from player boxes) */}
        <div className="betting-area">
          {gameState.players.map((player) => {
            const visualPosition = playerPositions.get(player.name) ?? 0;
            return player.bet > 0 ? (
              <div key={player.name} className={`bet-chips bet-position-${visualPosition}`}>
                <div className="chip-stack">
                  {(() => {
                    // Determine chip denomination and color based on bet amount
                    let chipValue, chipColor, numChips;
                    if (player.bet >= 100) {
                      chipValue = 100;
                      chipColor = 'black';
                      numChips = Math.min(Math.ceil(player.bet / 100), 4);
                    } else if (player.bet >= 25) {
                      chipValue = 25;
                      chipColor = player.bet >= 50 ? 'green' : 'blue';
                      numChips = Math.min(Math.ceil(player.bet / 25), 4);
                    } else {
                      chipValue = 5;
                      chipColor = 'red';
                      numChips = Math.min(Math.ceil(player.bet / 5), 4);
                    }
                    
                    return Array.from({ length: numChips }).map((_, chipIndex) => (
                      <div 
                        key={chipIndex} 
                        className={`poker-chip ${chipColor}`}
                        style={{ 
                          transform: `translateY(-${chipIndex * 2}px)`,
                          zIndex: chipIndex 
                        }}
                      >
                        ${chipValue}
                      </div>
                    ));
                  })()}
                </div>
                <div className="bet-amount">${player.bet}</div>
              </div>
            ) : null;
          })}
        </div>


        {/* AI Thinking Indicator - Full screen overlay (optional) */}
        {aiThinking && currentPlayer && !currentPlayer.is_human && useOverlayLoading && (
          <LoadingIndicator 
            currentPlayerName={currentPlayer.name}
            playerIndex={gameState.current_player_idx}
            totalPlayers={gameState.players.filter(p => !p.is_folded).length}
          />
        )}
      </div>

        {/* Winner Announcement */}
        <WinnerAnnouncement
          winnerInfo={winnerInfo}
          onComplete={() => setWinnerInfo(null)}
        />
      </div>
      </PokerTableLayout>

      {/* Original Elasticity Debug Panel for comparison */}
      <ElasticityDebugPanel 
        gameId={gameId} 
        isOpen={showElasticityPanel} 
        socket={socketRef.current} 
      />
    </>
  );
}