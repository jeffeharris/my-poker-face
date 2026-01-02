import { useEffect, useState, useCallback } from 'react';
import { Card, CommunityCard, HoleCard } from '../../cards';
import { ActionButtons } from '../ActionButtons';
import { ChatSidebar } from '../../chat/ChatSidebar';
import { LoadingIndicator } from '../LoadingIndicator';
import { PlayerThinking } from '../PlayerThinking';
import { WinnerAnnouncement } from '../WinnerAnnouncement';
import { TournamentComplete } from '../TournamentComplete';
import { ElasticityDebugPanel } from '../../debug/ElasticityDebugPanel';
import { PressureStats } from '../../stats';
import { PokerTableLayout } from '../PokerTableLayout';
import { DebugPanel } from '../../debug/DebugPanel';
import { config } from '../../../config';
import { usePokerGame } from '../../../hooks/usePokerGame';
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

interface PokerTableProps {
  gameId?: string | null;
  playerName?: string;
  onGameCreated?: (gameId: string) => void;
}

export function PokerTable({ gameId: providedGameId, playerName, onGameCreated }: PokerTableProps) {
  // Use the shared hook for all socket/state management
  const {
    gameState,
    loading,
    error,
    gameId,
    messages,
    aiThinking,
    winnerInfo,
    tournamentResult,
    socketRef,
    handlePlayerAction,
    handleSendMessage,
    clearWinnerInfo,
    clearTournamentResult,
  } = usePokerGame({
    gameId: providedGameId ?? null,
    playerName,
    onGameCreated,
  });

  // Handle tournament completion - clean up and return to menu
  const handleTournamentComplete = useCallback(async () => {
    if (gameId) {
      try {
        await fetch(`${config.API_URL}/end_game/${gameId}`, {
          method: 'POST',
          credentials: 'include',
        });
      } catch (err) {
        console.error('Failed to end game:', err);
      }
    }
    clearTournamentResult();
    localStorage.removeItem('activePokerGameId');
    // Navigate back to menu by reloading
    window.location.href = '/';
  }, [gameId, clearTournamentResult]);

  // Desktop-specific state
  const [useOverlayLoading] = useState(false);
  const [playerPositions, setPlayerPositions] = useState<Map<string, number>>(new Map());
  const [debugMode, setDebugMode] = useState<boolean>(config.ENABLE_DEBUG);
  const [showStats, setShowStats] = useState<boolean>(true);
  const [showElasticityPanel, setShowElasticityPanel] = useState<boolean>(false);
  const [pollIntervalRef, setPollIntervalRef] = useState<ReturnType<typeof setInterval> | null>(null);

  // Initialize player positions when game state loads
  useEffect(() => {
    if (gameState && playerPositions.size === 0) {
      const positions = new Map<string, number>();
      const humanIndex = gameState.players.findIndex((p: Player) => p.is_human);
      let positionIndex = 0;

      if (humanIndex !== -1) {
        positions.set(gameState.players[humanIndex].name, 0);
        positionIndex = 1;
      }

      gameState.players.forEach((player: Player) => {
        if (!player.is_human) {
          positions.set(player.name, positionIndex);
          positionIndex++;
        }
      });
      setPlayerPositions(positions);
    }
  }, [gameState, playerPositions.size]);

  // Cleanup polling on unmount
  useEffect(() => {
    return () => {
      if (pollIntervalRef) {
        clearInterval(pollIntervalRef);
      }
    };
  }, [pollIntervalRef]);

  // Polling function (kept for fallback if WebSocket fails)
  // @ts-expect-error Kept for fallback if WebSocket fails
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  const _startPolling = (gId: string) => {
    if (pollIntervalRef) {
      clearInterval(pollIntervalRef);
    }

    const pollInterval = setInterval(async () => {
      try {
        console.log('Polling for updates...');
        const gameResponse = await fetch(`${config.API_URL}/api/game-state/${gId}`, {
          credentials: 'include',
        });
        const data = await gameResponse.json();

        const currentPlayer = data.players[data.current_player_idx];
        console.log('Current player:', currentPlayer.name, 'Is human:', currentPlayer.is_human);

        if (currentPlayer.is_human || data.phase === 'GAME_OVER') {
          console.log('Stopping polling - human turn or game over');
          clearInterval(pollInterval);
          setPollIntervalRef(null);
        } else {
          console.log('AI still thinking...');
        }
      } catch (error) {
        console.error('Polling error:', error);
      }
    }, 1000);

    setPollIntervalRef(pollInterval);
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
            gameId={gameId ?? undefined}
            players={gameState?.players ?? []}
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
                  <div className="player-avatar">
                    {player.avatar_url ? (
                      <img
                        src={`${config.API_URL}${player.avatar_url}`}
                        alt={`${player.name} - ${player.avatar_emotion || 'avatar'}`}
                        className="avatar-image"
                      />
                    ) : (
                      <span className="avatar-initial">{player.name.charAt(0).toUpperCase()}</span>
                    )}
                  </div>
                  <div className="player-details">
                    <div className="player-name">{player.name}</div>
                    <div className="player-stack">${player.stack}</div>
                    {player.is_folded && <div className="status">FOLDED</div>}
                    {player.is_all_in && <div className="status">ALL-IN</div>}
                  </div>
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
          onComplete={clearWinnerInfo}
        />

        {/* Tournament Complete */}
        <TournamentComplete
          result={tournamentResult}
          onComplete={handleTournamentComplete}
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
