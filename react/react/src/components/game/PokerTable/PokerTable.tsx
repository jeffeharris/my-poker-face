import { useEffect, useState, useCallback } from 'react';
import { Card, CommunityCard, HoleCard, DebugHoleCard } from '../../cards';
import { ActionButtons } from '../ActionButtons';
import { ChatSidebar } from '../../chat/ChatSidebar';
import { PlayerThinking } from '../PlayerThinking';
import { WinnerAnnouncement } from '../WinnerAnnouncement';
import { TournamentComplete } from '../TournamentComplete';
import { PokerTableLayout } from '../PokerTableLayout';
import { StadiumLayout } from '../StadiumLayout';
import { GameHeader } from '../GameHeader';
import { PlayerCommandCenter } from '../PlayerCommandCenter';
import { StatsPanel } from '../StatsPanel';
import { ActivityFeed } from '../ActivityFeed';
import { config } from '../../../config';
import { usePokerGame } from '../../../hooks/usePokerGame';
import { useViewport } from '../../../hooks/useViewport';
import type { Player } from '../../../types/player';
import './PokerTable.css';

interface PokerTableProps {
  gameId?: string | null;
  playerName?: string;
  onGameCreated?: (gameId: string) => void;
}

// Breakpoint for stadium view (3-column layout)
const STADIUM_BREAKPOINT = 1400;

export function PokerTable({ gameId: providedGameId, playerName, onGameCreated }: PokerTableProps) {
  // Viewport detection for responsive layout
  const { width: viewportWidth } = useViewport();
  const useStadiumView = viewportWidth >= STADIUM_BREAKPOINT;

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
        await fetch(`${config.API_URL}/api/end_game/${gameId}`, {
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

  // Track fixed visual positions for players
  const [playerPositions, setPlayerPositions] = useState<Map<string, number>>(new Map());

  // Calculate seat position around the table based on player count
  // Position 0 is always at bottom center (human player)
  // Other positions are distributed clockwise around the table
  const getSeatStyle = (seatIndex: number, totalPlayers: number) => {
    // Start from bottom (90 degrees) and go clockwise
    // Angle 0 = right, 90 = bottom, 180 = left, 270 = top
    const startAngle = 90; // Bottom center (in degrees)
    const angleStep = 360 / totalPlayers;
    const angle = (startAngle + seatIndex * angleStep) * (Math.PI / 180);

    // Ellipse radii - seats positioned outside the table
    // Table is roughly 80% of container, so position seats at ~55% from center
    const radiusX = 55; // Horizontal radius as percentage
    const radiusY = 48; // Vertical radius as percentage (slightly less due to aspect ratio)

    // Calculate position on ellipse
    const left = 50 + radiusX * Math.cos(angle);
    const top = 50 + radiusY * Math.sin(angle);

    return {
      position: 'absolute' as const,
      left: `${left}%`,
      top: `${top}%`,
      transform: 'translate(-50%, -50%)',
    };
  };

  // Stadium view: Opponents in top 180 arc (human player shown in PlayerCommandCenter)
  const getStadiumSeatStyle = (opponentIndex: number, totalOpponents: number) => {
    // Distribute opponents from left (180) to right (0) across top arc
    // Angles: 150 (top-left) to 30 (top-right)
    const startAngle = 150; // degrees, left side
    const endAngle = 30;    // degrees, right side
    const angleRange = startAngle - endAngle; // 120 degrees of arc
    const angleStep = totalOpponents > 1 ? angleRange / (totalOpponents - 1) : 0;
    const angle = (startAngle - opponentIndex * angleStep) * (Math.PI / 180);

    // Wider ellipse for stadium view
    const radiusX = 48; // Horizontal radius as percentage
    const radiusY = 40; // Vertical radius as percentage

    // Calculate position on ellipse
    const left = 50 + radiusX * Math.cos(angle);
    const top = 50 - radiusY * Math.sin(angle); // Subtract to move up (top of screen)

    return {
      position: 'absolute' as const,
      left: `${left}%`,
      top: `${top}%`,
      transform: 'translate(-50%, -50%)',
    };
  };

  // Stadium view: Bet chips positioned closer to center
  const getStadiumBetChipStyle = (opponentIndex: number, totalOpponents: number) => {
    const startAngle = 150;
    const endAngle = 30;
    const angleRange = startAngle - endAngle;
    const angleStep = totalOpponents > 1 ? angleRange / (totalOpponents - 1) : 0;
    const angle = (startAngle - opponentIndex * angleStep) * (Math.PI / 180);

    const radiusX = 28;
    const radiusY = 22;

    const left = 50 + radiusX * Math.cos(angle);
    const top = 50 - radiusY * Math.sin(angle);

    return {
      position: 'absolute' as const,
      left: `${left}%`,
      top: `${top}%`,
      transform: 'translate(-50%, -50%)',
    };
  };

  // Calculate bet chip position (inside the table, closer to center)
  const getBetChipStyle = (seatIndex: number, totalPlayers: number) => {
    const startAngle = 90;
    const angleStep = 360 / totalPlayers;
    const angle = (startAngle + seatIndex * angleStep) * (Math.PI / 180);

    // Smaller radius - chips appear between player and center
    const radiusX = 28;
    const radiusY = 22;

    const left = 50 + radiusX * Math.cos(angle);
    const top = 50 + radiusY * Math.sin(angle);

    return {
      position: 'absolute' as const,
      left: `${left}%`,
      top: `${top}%`,
      transform: 'translate(-50%, -50%)',
    };
  };

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

  // Check if current player is human and it's their turn
  const currentPlayer = gameState?.players[gameState.current_player_idx];
  const showActionButtons = currentPlayer?.is_human &&
                           !currentPlayer.is_folded &&
                           gameState?.player_options &&
                           gameState.player_options.length > 0 &&
                           !aiThinking;

  // Stadium view helpers
  const humanPlayer = gameState?.players.find((p: Player) => p.is_human);
  const humanPlayerIndex = gameState?.players.findIndex((p: Player) => p.is_human) ?? -1;
  const opponents = gameState?.players.filter((p: Player) => !p.is_human) ?? [];
  const isHumanDealer = humanPlayerIndex === gameState?.current_dealer_idx;
  const isHumanSmallBlind = humanPlayerIndex === gameState?.small_blind_idx;
  const isHumanBigBlind = humanPlayerIndex === gameState?.big_blind_idx;
  const isHumanCurrentPlayer = humanPlayerIndex === gameState?.current_player_idx;

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

  // Shared table content - community cards, pot, and overlays
  const renderTableCore = () => (
    <>
      {/* Community Cards Area */}
      <div className="community-area">
        <div className="community-cards">
          {gameState.community_cards.map((card, i) => (
            <CommunityCard key={i} card={card} revealed={true} />
          ))}
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

      {/* Winner Announcement */}
      <WinnerAnnouncement
        winnerInfo={winnerInfo}
        onComplete={clearWinnerInfo}
      />

      {/* Tournament Complete */}
      {!(winnerInfo?.is_final_hand) && (
        <TournamentComplete
          result={tournamentResult}
          onComplete={handleTournamentComplete}
        />
      )}
    </>
  );

  // Render chip for a bet
  const renderBetChips = (bet: number) => {
    let chipValue: number, chipColor: string, numChips: number;
    if (bet >= 100) {
      chipValue = 100;
      chipColor = 'black';
      numChips = Math.min(Math.ceil(bet / 100), 4);
    } else if (bet >= 25) {
      chipValue = 25;
      chipColor = bet >= 50 ? 'green' : 'blue';
      numChips = Math.min(Math.ceil(bet / 25), 4);
    } else {
      chipValue = 5;
      chipColor = 'red';
      numChips = Math.min(Math.ceil(bet / 5), 4);
    }

    return (
      <>
        <div className="chip-stack">
          {Array.from({ length: numChips }).map((_, chipIndex) => (
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
          ))}
        </div>
        <div className="bet-amount">${bet}</div>
      </>
    );
  };

  // ==========================================
  // STADIUM VIEW (Desktop >= 1400px)
  // ==========================================
  if (useStadiumView) {
    return (
      <StadiumLayout
        header={
          <GameHeader
            handNumber={gameState.hand_number}
            blinds={{ small: gameState.small_blind, big: gameState.big_blind }}
            phase={gameState.phase}
            onBackClick={() => { window.location.href = '/'; }}
          />
        }
        leftPanel={
          humanPlayer && (
            <StatsPanel
              humanPlayer={humanPlayer}
              players={gameState.players}
              potTotal={gameState.pot.total}
              handNumber={gameState.hand_number}
            />
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
            />
          )
        }
        rightPanel={
          <ActivityFeed
            messages={messages}
            onSendMessage={handleSendMessage}
            players={gameState?.players ?? []}
            playerName={playerName}
          />
        }
      >
        <div className="poker-table stadium-view">
          <div className="table-felt">
            {renderTableCore()}

            {/* Opponents in top arc */}
            <div className="players-area">
              {opponents.map((player, opponentIndex) => {
                const currentIndex = gameState.players.findIndex(p => p.name === player.name);
                const isDealer = currentIndex === gameState.current_dealer_idx;
                const isSmallBlind = currentIndex === gameState.small_blind_idx;
                const isBigBlind = currentIndex === gameState.big_blind_idx;
                const isCurrentPlayer = currentIndex === gameState.current_player_idx;

                return (
                  <div
                    key={player.name}
                    className={`player-seat ${
                      isCurrentPlayer ? 'current-player' : ''
                    } ${player.is_folded ? 'folded' : ''} ${player.is_all_in ? 'all-in' : ''} ${
                      isCurrentPlayer && aiThinking ? 'thinking' : ''
                    }`}
                    style={getStadiumSeatStyle(opponentIndex, opponents.length)}
                  >
                    <div className="position-indicators">
                      {isDealer && <div className="position-chip dealer-button" title="Dealer">D</div>}
                      {isSmallBlind && <div className="position-chip small-blind" title="Small Blind">SB</div>}
                      {isBigBlind && <div className="position-chip big-blind" title="Big Blind">BB</div>}
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
                        {player.bet > 0 && <div className="player-bet">Bet: ${player.bet}</div>}
                        {player.is_folded && <div className="status">FOLDED</div>}
                        {player.is_all_in && <div className="status">ALL-IN</div>}
                      </div>
                    </div>

                    <div className="player-cards">
                      {config.ENABLE_AI_DEBUG ? (
                        <>
                          <DebugHoleCard debugInfo={player.llm_debug} />
                          <DebugHoleCard debugInfo={player.llm_debug} />
                        </>
                      ) : (
                        <>
                          <HoleCard visible={false} />
                          <HoleCard visible={false} />
                        </>
                      )}
                    </div>

                    {isCurrentPlayer && aiThinking && (
                      <PlayerThinking playerName={player.name} position={opponentIndex} />
                    )}
                  </div>
                );
              })}
            </div>

            {/* Bet chips hidden in stadium view - bets shown in player seats */}
          </div>
        </div>
      </StadiumLayout>
    );
  }

  // ==========================================
  // LEGACY VIEW (Tablet/Mobile < 1400px)
  // ==========================================
  return (
    <>
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
      >
        <div className="poker-table">
          <div className="table-felt">
            {renderTableCore()}

            {/* Player seats around ellipse */}
            <div className="players-area">
              {[...gameState.players]
                .sort((a, b) => {
                  const posA = playerPositions.get(a.name) ?? 0;
                  const posB = playerPositions.get(b.name) ?? 0;
                  return posA - posB;
                })
                .map((player) => {
                  const currentIndex = gameState.players.findIndex(p => p.name === player.name);
                  const visualPosition = playerPositions.get(player.name) ?? 0;
                  const isDealer = currentIndex === gameState.current_dealer_idx;
                  const isSmallBlind = currentIndex === gameState.small_blind_idx;
                  const isBigBlind = currentIndex === gameState.big_blind_idx;
                  const isCurrentPlayer = currentIndex === gameState.current_player_idx;
                  const totalPlayers = gameState.players.length;

                  return (
                    <div
                      key={player.name}
                      className={`player-seat ${
                        isCurrentPlayer ? 'current-player' : ''
                      } ${player.is_folded ? 'folded' : ''} ${player.is_all_in ? 'all-in' : ''} ${
                        isCurrentPlayer && !player.is_human && aiThinking ? 'thinking' : ''
                      }`}
                      style={getSeatStyle(visualPosition, totalPlayers)}
                    >
                      <div className="position-indicators">
                        {isDealer && <div className="position-chip dealer-button" title="Dealer">D</div>}
                        {isSmallBlind && <div className="position-chip small-blind" title="Small Blind">SB</div>}
                        {isBigBlind && <div className="position-chip big-blind" title="Big Blind">BB</div>}
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

                      <div className="player-cards">
                        {player.is_human && player.hand ? (
                          <>
                            <Card card={player.hand[0]} faceDown={false} size="large" className="hole-card" />
                            <Card card={player.hand[1]} faceDown={false} size="large" className="hole-card" />
                          </>
                        ) : config.ENABLE_AI_DEBUG ? (
                          <>
                            <DebugHoleCard debugInfo={player.llm_debug} />
                            <DebugHoleCard debugInfo={player.llm_debug} />
                          </>
                        ) : (
                          <>
                            <HoleCard visible={false} />
                            <HoleCard visible={false} />
                          </>
                        )}
                      </div>

                      {isCurrentPlayer && !player.is_human && aiThinking && (
                        <PlayerThinking playerName={player.name} position={visualPosition} />
                      )}
                    </div>
                  );
                })}
            </div>

            {/* Bet chips */}
            <div className="betting-area">
              {gameState.players.map((player) => {
                const visualPosition = playerPositions.get(player.name) ?? 0;
                const totalPlayers = gameState.players.length;
                return player.bet > 0 ? (
                  <div
                    key={player.name}
                    className="bet-chips"
                    style={getBetChipStyle(visualPosition, totalPlayers)}
                  >
                    {renderBetChips(player.bet)}
                  </div>
                ) : null;
              })}
            </div>
          </div>
        </div>
      </PokerTableLayout>
    </>
  );
}
