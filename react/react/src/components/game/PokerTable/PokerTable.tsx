import { useCallback, useMemo, useRef, useState } from 'react';
import { Bot } from 'lucide-react';
import { CommunityCard, HoleCard, DebugHoleCard } from '../../cards';
import { CharacterDetailCard } from '../../character';
import { dossierFromPlayer } from '../../character/dossierFromPlayer';
import { PlayerThinking } from '../PlayerThinking';
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
import { ActionBadge } from '../../shared';
import { ShuffleLoading } from '../../shared/ShuffleLoading';
import { pickQuote } from '../WinnerAnnouncement/quote-flavor';
import { useGuestChatLimit } from '../../../hooks/useGuestChatLimit';
import { logger } from '../../../utils/logger';
import { gameAPI } from '../../../utils/api';
import { config } from '../../../config';
import { usePokerGame } from '../../../hooks/usePokerGame';
import { isBettingPhase } from '../../../constants/gamePhases';
import type { Player } from '../../../types/player';
import '../../../styles/action-badges.css';
import './PokerTable.css';

interface PokerTableProps {
  gameId?: string | null;
  playerName?: string;
  onGameCreated?: (gameId: string) => void;
  /** Parent's back handler. Falls back to `window.location.href = '/'`
   *  if omitted, matching the legacy desktop behavior. */
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

  // Character dossier — opens when an opponent avatar is clicked.
  const [dossierPlayer, setDossierPlayer] = useState<Player | null>(null);
  const [dossierOrigin, setDossierOrigin] = useState<{ x: number; y: number } | undefined>();
  const openDossierForPlayer = useCallback((player: Player, target: HTMLElement) => {
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
    tournamentResult,
    socketRef: _socketRef,
    isConnected: _isConnected,
    showActionButtons,
    handlePlayerAction,
    handleSendMessage,
    clearWinnerInfo,
    clearTournamentResult,
    cashBustEvent,
    clearCashBustEvent,
  } = usePokerGame({
    gameId: providedGameId ?? null,
    playerName,
    onGameCreated,
    onGameLoadFailed,
  });

  // Desktop chat is free-text only (no quick-chat panel), so it's gated by the
  // guest free-chat lock rather than the per-turn quick-chat limit.
  const { wrappedSendMessage, guestFreeChatLocked } = useGuestChatLimit(
    gameState?.awaiting_action,
    handleSendMessage
  );

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
    // Navigate back to menu by reloading
    window.location.href = '/';
  }, [gameId, clearTournamentResult]);

  // Calculate seat position based on offset from human player
  // Players are anchored to fixed positions - only dealer/blind buttons rotate
  // Seat offset 1 (acts after human) = left side, higher offsets move right
  const getStadiumSeatStyle = (
    seatOffset: number,
    totalPlayers: number,
    headsUpShowdownSlot?: number
  ) => {
    if (headsUpShowdownSlot !== undefined) {
      const left = headsUpShowdownSlot === 0 ? 25 : 75;
      return {
        position: 'absolute' as const,
        left: `${left}%`,
        top: '24%',
        transform: 'translate(-50%, -50%) scale(1)',
      };
    }

    // Total opponents is totalPlayers - 1 (excluding human)
    const totalOpponents = totalPlayers - 1;

    // Map seat offset (1 to totalOpponents) to position index (0 to totalOpponents-1)
    // seatOffset 1 = leftmost (index 0), seatOffset N-1 = rightmost (index N-2)
    const positionIndex = seatOffset - 1;

    // Dynamic arc spread - tighter when fewer opponents to keep them closer together
    // Full arc (120°) for 5+ opponents, narrower for fewer
    const maxArcSpread = 120;
    const minArcSpread = 60; // For 2 opponents
    const arcSpread = totalOpponents <= 2 ? minArcSpread : totalOpponents <= 4 ? 80 : maxArcSpread;

    // Center the arc around 90° (top center)
    const centerAngle = 90;
    const startAngle = centerAngle + arcSpread / 2; // left side
    const endAngle = centerAngle - arcSpread / 2; // right side
    const angleRange = startAngle - endAngle;
    const angleStep = totalOpponents > 1 ? angleRange / (totalOpponents - 1) : 0;
    const angle = (startAngle - positionIndex * angleStep) * (Math.PI / 180);

    // Wider ellipse for stadium view - reduced radiusY to bring avatars down
    const radiusX = 42; // Horizontal radius as percentage
    const radiusY = 28; // Vertical radius as percentage (reduced to bring avatars down)

    // Calculate position on ellipse, with offset to clear the header
    const left = 50 + radiusX * Math.cos(angle);
    const top = 52 - radiusY * Math.sin(angle); // Start from 52% to position avatars

    // Dynamic scaling - larger cards when fewer opponents
    const scale = totalOpponents <= 2 ? 1.6 : totalOpponents <= 4 ? 1.3 : 1.0;

    return {
      position: 'absolute' as const,
      left: `${left}%`,
      top: `${top}%`,
      transform: `translate(-50%, -50%) scale(${scale})`,
    };
  };

  // Stadium view helpers
  const humanPlayer = gameState?.players.find((p: Player) => p.is_human);
  const humanPlayerIndex = gameState?.players.findIndex((p: Player) => p.is_human) ?? -1;
  const opponents = gameState?.players.filter((p: Player) => !p.is_human) ?? [];
  const showdownOpponents = opponents.filter((p: Player) => !p.is_folded);
  const isHeadsUpShowdownLayout =
    (gameState?.run_it_out || gameState?.phase === 'SHOWDOWN') && showdownOpponents.length === 2;
  const isHumanDealer = humanPlayerIndex === gameState?.current_dealer_idx;
  const isHumanSmallBlind = humanPlayerIndex === gameState?.small_blind_idx;
  const isHumanBigBlind = humanPlayerIndex === gameState?.big_blind_idx;

  // Don't highlight active player during run-it-out, non-betting phases, or when phase is not set
  const phase = gameState?.phase;
  const shouldHighlightActivePlayer = isBettingPhase(phase, gameState?.run_it_out);
  const isHumanCurrentPlayer =
    shouldHighlightActivePlayer && humanPlayerIndex === gameState?.current_player_idx;

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
      {/* Community Cards Area */}
      <div className="community-area">
        <div className="pot-area">
          <div className="pot">
            <div className="pot-label">POT</div>
            <div className="pot-amount">${gameState.pot.total}</div>
          </div>
        </div>

        <div className="community-cards">
          {gameState.community_cards.map((card, i) => (
            <CommunityCard key={i} card={card} revealed={true} />
          ))}
          {Array.from({ length: 5 - gameState.community_cards.length }).map((_, i) => (
            <CommunityCard key={`placeholder-${i}`} revealed={false} />
          ))}
        </div>
      </div>

      {/* Winner Announcement */}
      <WinnerAnnouncement
        winnerInfo={winnerInfo}
        onComplete={clearWinnerInfo}
        players={gameState.players}
      />

      {/* Tournament Complete */}
      {!winnerInfo?.is_final_hand && (
        <TournamentComplete result={tournamentResult} onComplete={handleTournamentComplete} />
      )}
    </>
  );

  // Stadium Layout - used for all desktop screen sizes
  return (
    <>
      <BustModal event={cashBustEvent} onDismiss={clearCashBustEvent} />
      <SoloTableModal cashMode={gameState.cash_mode} />
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
                window.location.href = '/';
              })
            }
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
                  playerFolded={!!humanPlayer.is_folded}
                />
              )}
              <StatsPanel
                humanPlayer={humanPlayer}
                players={gameState.players}
                potTotal={gameState.pot.total}
                handNumber={gameState.hand_number}
              />
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
              onFastForward={
                gameId
                  ? (enabled: boolean) => {
                      gameAPI.fastForward(gameId, enabled).catch((e) => {
                        logger.warn('[FF] toggle failed', e);
                      });
                    }
                  : undefined
              }
            />
          )
        }
        rightPanel={
          <ActivityFeed
            messages={messages}
            onSendMessage={wrappedSendMessage}
            playerName={playerName}
            guestChatDisabled={guestFreeChatLocked}
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

                const isDealer = playerIndex === gameState.current_dealer_idx;
                const isSmallBlind = playerIndex === gameState.small_blind_idx;
                const isBigBlind = playerIndex === gameState.big_blind_idx;
                const isCurrentPlayer =
                  shouldHighlightActivePlayer && playerIndex === gameState.current_player_idx;

                // Compute avatar state: swap to "thinking" when AI is processing
                const isAiThinking = isCurrentPlayer && aiThinking && !player.is_human;
                const avatarUrl =
                  isAiThinking && player.avatar_url
                    ? player.avatar_url.replace(
                        /\/api\/avatar\/(.+?)\/[^/]+(\/full)?$/,
                        '/api/avatar/$1/thinking$2'
                      )
                    : player.avatar_url;
                const avatarEmotion = isAiThinking ? 'thinking' : player.avatar_emotion || 'avatar';
                const headsUpShowdownSlot = isHeadsUpShowdownLayout
                  ? showdownOpponents.findIndex((p: Player) => p.name === player.name)
                  : -1;

                return (
                  <div
                    key={player.name}
                    className={`player-seat ${
                      isCurrentPlayer ? 'current-player' : ''
                    } ${player.is_folded ? 'folded' : ''} ${player.is_all_in ? 'all-in' : ''} ${
                      isCurrentPlayer && aiThinking ? 'thinking' : ''
                    }`}
                    style={getStadiumSeatStyle(
                      seatOffset,
                      totalPlayers,
                      headsUpShowdownSlot >= 0 ? headsUpShowdownSlot : undefined
                    )}
                  >
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
                      <button
                        type="button"
                        className="player-avatar player-avatar--clickable"
                        onClick={(e) =>
                          openDossierForPlayer(player, e.currentTarget as HTMLElement)
                        }
                        aria-label={`Open dossier for ${player.name}`}
                      >
                        {avatarUrl ? (
                          <img
                            src={`${config.API_URL}${avatarUrl}`}
                            alt={`${player.name} - ${avatarEmotion}`}
                            className={`avatar-image${isAiThinking ? ' avatar-thinking' : ''}`}
                          />
                        ) : (
                          <span className="avatar-initial">
                            {player.name.charAt(0).toUpperCase()}
                          </span>
                        )}
                        {player.is_rule_bot && (
                          <span className="bot-badge" title="Rule-based training bot">
                            <Bot size={14} aria-hidden />
                          </span>
                        )}
                      </button>
                      <div className="player-details">
                        <div className="player-name">{player.name}</div>
                        <div className="player-stack">${player.stack}</div>
                        {player.bet > 0 && <div className="player-bet">Bet: ${player.bet}</div>}
                        <ActionBadge
                          player={player}
                          lastKnownActions={lastKnownActions}
                          onFadeComplete={() => setFadeKey((k) => k + 1)}
                        />
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
                          <HoleCard visible={false} size="xsmall" />
                          <HoleCard visible={false} size="xsmall" />
                        </>
                      )}
                    </div>

                    {isCurrentPlayer && aiThinking && (
                      <PlayerThinking playerName={player.name} position={seatOffset} />
                    )}
                  </div>
                );
              })}
            </div>

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
      />
    </>
  );
}
