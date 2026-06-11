/**
 * PlayerSeat — one opponent seat on the desktop stadium felt: position chips
 * (D/SB/BB), avatar (with the "thinking" emotion swap), name/stack/bet, action
 * badge, hole cards (revealed at showdown / debug / hidden), the thinking
 * indicator, and the speech bubble. Extracted verbatim from PokerTable's
 * `opponents.map` body.
 *
 * Memoized: with the store's structural sharing the `player` ref is stable when
 * unchanged, and every other prop is a primitive or a reference-stable
 * callback/ref — so unchanged seats skip re-render as the table updates around
 * them. (Keep it that way: never pass an inline arrow/object as a prop here.)
 */

import { memo } from 'react';
import { AnimatePresence } from 'framer-motion';
import { Bot } from 'lucide-react';
import type { MutableRefObject } from 'react';
import { Card, HoleCard, DebugHoleCard } from '../../cards';
import { PlayerThinking } from '../PlayerThinking';
import { SeatSpeechBubble } from '../SeatSpeechBubble/SeatSpeechBubble';
import { ActionBadge } from '../../shared';
import { CountUp } from '../../shared/CountUp';
import { avatarUrlForEmotion } from '../../../utils/avatarUrl';
import { config } from '../../../config';
import type { Player } from '../../../types/player';
import type { ChatMessage, RevealedCardsInfo } from '../../../types';
import { getStadiumSeatStyle } from './stadiumSeat';

export const PlayerSeat = memo(function PlayerSeat({
  player,
  displayName,
  seatOffset,
  totalPlayers,
  headsUpShowdownSlot,
  isDealer,
  isSmallBlind,
  isBigBlind,
  isCurrentPlayer,
  aiThinking,
  isSpeaking,
  speechMessage,
  revealedCards,
  revealIndex,
  lastKnownActions,
  onOpenDossier,
  onFadeComplete,
  onDismissSpeech,
}: {
  player: Player;
  displayName: string;
  seatOffset: number;
  totalPlayers: number;
  headsUpShowdownSlot?: number;
  isDealer: boolean;
  isSmallBlind: boolean;
  isBigBlind: boolean;
  isCurrentPlayer: boolean;
  aiThinking: boolean;
  isSpeaking: boolean;
  speechMessage: ChatMessage | null;
  revealedCards: RevealedCardsInfo['players_cards'][string] | undefined;
  revealIndex: number;
  lastKnownActions: MutableRefObject<Map<string, string>>;
  onOpenDossier: (player: Player, target: HTMLElement) => void;
  onFadeComplete: () => void;
  onDismissSpeech: () => void;
}) {
  // Compute avatar state: swap to "thinking" when this AI is processing.
  const isAiThinking = isCurrentPlayer && aiThinking && !player.is_human;
  const avatarUrl = isAiThinking
    ? avatarUrlForEmotion(player.avatar_url, 'thinking')
    : player.avatar_url;
  const avatarEmotion = isAiThinking ? 'thinking' : player.avatar_emotion || 'avatar';

  return (
    <div
      className={`player-seat ${isCurrentPlayer ? 'current-player' : ''} ${
        player.is_folded ? 'folded' : ''
      } ${player.is_all_in ? 'all-in' : ''} ${isCurrentPlayer && aiThinking ? 'thinking' : ''}${
        isSpeaking ? ' is-speaking' : ''
      }`}
      style={getStadiumSeatStyle(seatOffset, totalPlayers, headsUpShowdownSlot)}
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
          onClick={(e) => onOpenDossier(player, e.currentTarget as HTMLElement)}
          aria-label={`Open dossier for ${player.name}`}
        >
          {avatarUrl ? (
            <img
              src={`${config.API_URL}${avatarUrl}`}
              alt={`${player.name} - ${avatarEmotion}`}
              className={`avatar-image${isAiThinking ? ' avatar-thinking' : ''}`}
            />
          ) : (
            <span className="avatar-initial">{player.name.charAt(0).toUpperCase()}</span>
          )}
          {player.is_rule_bot && (
            <span className="bot-badge" title="Rule-based training bot">
              <Bot size={14} aria-hidden />
            </span>
          )}
        </button>
        <div className="player-details">
          <div className="player-name">{displayName}</div>
          <div className="player-stack">
            $<CountUp value={player.stack} />
          </div>
          {player.bet > 0 && (
            <div className="player-bet">
              Bet: $<CountUp value={player.bet} from={0} />
            </div>
          )}
          <ActionBadge
            player={player}
            lastKnownActions={lastKnownActions}
            onFadeComplete={onFadeComplete}
          />
        </div>
      </div>

      {/* Hole cards: revealed face-up during showdown, hidden otherwise */}
      {revealedCards ? (
        <div
          className="player-revealed-cards"
          style={{ '--reveal-index': revealIndex } as React.CSSProperties}
        >
          {revealedCards.map((card, i) => (
            <Card key={i} card={card} faceDown={false} size="small" />
          ))}
        </div>
      ) : (
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
      )}

      {isCurrentPlayer && aiThinking && (
        <PlayerThinking playerName={player.name} position={seatOffset} />
      )}

      {/* Chat bubble pops up beneath the seat of the speaker. */}
      <AnimatePresence>
        {isSpeaking && speechMessage && (
          <SeatSpeechBubble message={speechMessage} onDismiss={onDismissSpeech} />
        )}
      </AnimatePresence>
    </div>
  );
});
