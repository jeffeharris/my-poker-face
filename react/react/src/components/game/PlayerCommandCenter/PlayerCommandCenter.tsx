import { Card } from '../../cards';
import { ActionButtons } from '../ActionButtons';
import type { Player } from '../../../types/player';
import { config } from '../../../config';
import './PlayerCommandCenter.css';

interface PlayerCommandCenterProps {
  player: Player;
  isCurrentPlayer: boolean;
  showActions: boolean;
  playerOptions: string[];
  highestBet: number;
  minRaise: number;
  bigBlind: number;
  potSize: number;
  onAction: (action: string, amount?: number) => void;
  isDealer: boolean;
  isSmallBlind: boolean;
  isBigBlind: boolean;
}

export function PlayerCommandCenter({
  player,
  isCurrentPlayer,
  showActions,
  playerOptions,
  highestBet,
  minRaise,
  bigBlind,
  potSize,
  onAction,
  isDealer,
  isSmallBlind,
  isBigBlind,
}: PlayerCommandCenterProps) {
  const costToCall = Math.max(0, highestBet - player.bet);

  return (
    <div
      className={`player-command-center ${
        isCurrentPlayer ? 'is-active' : ''
      } ${player.is_folded ? 'is-folded' : ''} ${player.is_all_in ? 'is-all-in' : ''}`}
    >
      {/* Top section: Cards + Info */}
      <div className="command-center__top">
        {/* Player cards (larger display) */}
        <div className="command-center__cards">
          {player.hand && player.hand.length >= 2 ? (
            <>
              <Card card={player.hand[0]} faceDown={false} size="large" className="command-card" />
              <Card card={player.hand[1]} faceDown={false} size="large" className="command-card" />
            </>
          ) : (
            <>
              <div className="command-card placeholder" />
              <div className="command-card placeholder" />
            </>
          )}
        </div>

        {/* Player info */}
        <div className="command-center__info">
          <div className="command-center__avatar">
            {player.avatar_url ? (
              <img
                src={`${config.API_URL}${player.avatar_url}`}
                alt={player.name}
                className="avatar-image"
              />
            ) : (
              <span className="avatar-initial">{player.name.charAt(0).toUpperCase()}</span>
            )}
          </div>
          <div className="command-center__details">
            <div className="command-center__name">{player.name}</div>
            <div className="command-center__stack">
              <span className="stack-label">Stack:</span>
              <span className="stack-value">${player.stack.toLocaleString()}</span>
            </div>
            {player.bet > 0 && (
              <div className="command-center__current-bet">
                Bet: ${player.bet}
              </div>
            )}
            {costToCall > 0 && !player.is_folded && (
              <div className="command-center__to-call">
                To call: ${costToCall}
              </div>
            )}
          </div>
        </div>

        {/* Position indicators */}
        <div className="command-center__positions">
          {isDealer && (
            <div className="position-chip dealer" title="Dealer">D</div>
          )}
          {isSmallBlind && (
            <div className="position-chip small-blind" title="Small Blind">SB</div>
          )}
          {isBigBlind && (
            <div className="position-chip big-blind" title="Big Blind">BB</div>
          )}
        </div>

        {/* Status badges */}
        <div className="command-center__status">
          {player.is_folded && <span className="status-badge folded">FOLDED</span>}
          {player.is_all_in && <span className="status-badge all-in">ALL-IN</span>}
        </div>
      </div>

      {/* Bottom section: Action buttons (when it's our turn) */}
      {showActions && (
        <div className="command-center__actions">
          <ActionButtons
            playerOptions={playerOptions}
            currentPlayerStack={player.stack}
            highestBet={highestBet}
            currentPlayerBet={player.bet}
            minRaise={minRaise}
            bigBlind={bigBlind}
            potSize={potSize}
            onAction={onAction}
            inline={true}
          />
        </div>
      )}
    </div>
  );
}
