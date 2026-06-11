/**
 * MobileOpponents — the opponents region: the showdown ghost rail (folded
 * players as small circles), the horizontally-scrolling opponents row with
 * per-seat avatar/name/stack/bet/revealed-cards/action-badge, and the heads-up
 * psychology panel. Extracted verbatim from MobilePokerTable.
 *
 * Refs (`containerRef`, `opponentRefs`) are owned by MobilePokerTable — it runs
 * the auto-scroll-to-active effect against them — and threaded down so the DOM
 * nodes register here.
 */

import type { CSSProperties, MutableRefObject, RefObject } from 'react';
import { Bot } from 'lucide-react';
import type { Player } from '../../types/player';
import type { RevealedCardsInfo } from '../../types';
import { Card } from '../cards';
import { ActionBadge } from '../shared';
import { CountUp } from '../shared/CountUp';
import { HeadsUpOpponentPanel } from './HeadsUpOpponentPanel';
import { avatarUrlForEmotion } from '../../utils/avatarUrl';
import { config } from '../../config';

type NicknameSubject = { name: string; nickname?: string | null };

export function MobileOpponents({
  opponents,
  activeOpponents,
  foldedOpponents,
  isInShowdown,
  isShowdown,
  storePlayers,
  currentPlayerIdx,
  dealerIdx,
  shouldHighlightActivePlayer,
  aiThinking,
  isHeadsUp,
  isTwoOpponents,
  isThreeOpponents,
  isThreeOpponentsNormal,
  isThreeOpponentsShowdown,
  headsUpOpponent,
  providedGameId,
  humanPlayerName,
  displayNickname,
  revealedCards,
  revealOrder,
  lastKnownActions,
  onFadeComplete,
  containerRef,
  opponentRefs,
  onOpenDebug,
  onOpenDossier,
}: {
  opponents: Player[];
  activeOpponents: Player[];
  foldedOpponents: Player[];
  isInShowdown: boolean;
  isShowdown: boolean;
  storePlayers: Player[];
  currentPlayerIdx: number;
  dealerIdx: number;
  shouldHighlightActivePlayer: boolean;
  aiThinking: boolean;
  isHeadsUp: boolean;
  isTwoOpponents: boolean;
  isThreeOpponents: boolean;
  isThreeOpponentsNormal: boolean;
  isThreeOpponentsShowdown: boolean;
  headsUpOpponent: Player | null;
  providedGameId?: string | null;
  humanPlayerName?: string;
  displayNickname: (player: NicknameSubject) => string;
  revealedCards: RevealedCardsInfo | null;
  revealOrder: Map<string, number>;
  lastKnownActions: MutableRefObject<Map<string, string>>;
  onFadeComplete: () => void;
  containerRef: RefObject<HTMLDivElement | null>;
  opponentRefs: MutableRefObject<Map<string, HTMLDivElement>>;
  onOpenDebug: (player: Player) => void;
  onOpenDossier: (player: Player, target: HTMLElement) => void;
}) {
  return (
    <div className={`opponents-wrapper ${isInShowdown ? 'showdown-mode' : ''}`}>
      {/* Ghost Rail - folded players as small circles during showdown */}
      {isInShowdown && foldedOpponents.length > 0 && (
        <div className="ghost-rail" data-testid="ghost-rail">
          {foldedOpponents.map((player) => (
            <div key={player.name} className="ghost-avatar" title={displayNickname(player)}>
              {player.avatar_url ? (
                <img src={`${config.API_URL}${player.avatar_url}`} alt={displayNickname(player)} />
              ) : (
                <span className="ghost-initial">{player.name.charAt(0).toUpperCase()}</span>
              )}
            </div>
          ))}
        </div>
      )}

      {/* Opponents Row - shows only active players during showdown */}
      <div
        className={[
          'mobile-opponents',
          isHeadsUp && 'heads-up-mode',
          isTwoOpponents && 'two-opponents-mode',
          isThreeOpponentsNormal && 'three-opponents-mode',
          isThreeOpponentsShowdown && 'three-opponents-showdown-mode',
        ]
          .filter(Boolean)
          .join(' ')}
        data-testid="mobile-opponents"
        ref={containerRef}
      >
        {(isInShowdown ? activeOpponents : opponents).map((opponent) => {
          const opponentIdx = storePlayers.findIndex((p) => p.name === opponent.name);
          const isCurrentPlayer = shouldHighlightActivePlayer && opponentIdx === currentPlayerIdx;
          const isDealer = opponentIdx === dealerIdx;
          const isDebugEnabled = config.ENABLE_AI_DEBUG && !!opponent.llm_debug;

          // Swap the avatar image to the "thinking" emotion variant
          // when the AI for this seat is the one currently deciding.
          // Mirrors PokerTable.tsx — the backend serves a per-emotion
          // image at /api/avatar/{name}/{emotion}, so we rewrite the
          // URL rather than just toggling a CSS class. Without the
          // rewrite the same default avatar shows the whole hand
          // even though the player object exposes avatar_emotion.
          const isAiThinking = isCurrentPlayer && aiThinking && !opponent.is_human;
          const avatarUrl = isAiThinking
            ? avatarUrlForEmotion(opponent.avatar_url, 'thinking')
            : opponent.avatar_url;
          const avatarEmotion = isAiThinking ? 'thinking' : opponent.avatar_emotion || 'avatar';

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
              className={[
                'mobile-opponent',
                opponent.is_folded && 'folded',
                opponent.is_all_in && 'all-in',
                isCurrentPlayer && !isInShowdown && 'thinking',
                isHeadsUp && 'heads-up-avatar',
                isTwoOpponents && 'two-opponents-avatar',
                isThreeOpponents && 'three-opponents-avatar',
              ]
                .filter(Boolean)
                .join(' ')}
              data-testid="mobile-opponent"
            >
              <div
                className={`opponent-avatar ${isDebugEnabled ? 'debug-enabled' : 'dossier-enabled'}`}
                onClick={
                  isDebugEnabled
                    ? () => onOpenDebug(opponent)
                    : (e) => onOpenDossier(opponent, e.currentTarget as HTMLElement)
                }
                role="button"
                tabIndex={0}
                aria-label={
                  isDebugEnabled
                    ? `View ${opponent.name}'s AI model info`
                    : `Open dossier for ${opponent.name}`
                }
              >
                {avatarUrl ? (
                  <img
                    src={`${config.API_URL}${avatarUrl}`}
                    alt={`${opponent.name} - ${avatarEmotion}`}
                    className={`avatar-image ${isAiThinking ? 'avatar-image--thinking' : ''} ${
                      isShowdown ? 'avatar-image--showdown' : ''
                    }`}
                    onLoad={(e) => {
                      // Clear any display:none left over from a prior 404.
                      // Without this, the avatar stays hidden after switching
                      // back from a missing /thinking variant to a valid URL.
                      e.currentTarget.style.display = '';
                    }}
                    onError={(e) => {
                      const img = e.currentTarget;
                      // If the rewritten /thinking variant 404s, fall back to
                      // the server-provided avatar_url (which has its own
                      // emotion fallback). Avoids loop by tracking attempt.
                      if (
                        isAiThinking &&
                        opponent.avatar_url &&
                        img.dataset.thinkingFallbackTried !== 'true' &&
                        img.src !== `${config.API_URL}${opponent.avatar_url}`
                      ) {
                        img.dataset.thinkingFallbackTried = 'true';
                        img.src = `${config.API_URL}${opponent.avatar_url}`;
                        return;
                      }
                      img.style.display = 'none';
                    }}
                  />
                ) : (
                  opponent.name.charAt(0).toUpperCase()
                )}
                {isDealer && <span className="dealer-badge">D</span>}
                {opponent.is_rule_bot && (
                  <span className="bot-badge" title="Rule-based training bot">
                    <Bot size={12} aria-hidden />
                  </span>
                )}
                {/* Debug indicator badge */}
                {config.ENABLE_AI_DEBUG && opponent.llm_debug && (
                  <span className="debug-badge" title="Tap to view AI model info"></span>
                )}
              </div>
              <div className="opponent-info">
                <span className="opponent-name" data-testid="opponent-name">
                  {displayNickname(opponent)}
                </span>
                <span className="opponent-stack" data-testid="opponent-stack">
                  $<CountUp value={opponent.stack} />
                </span>
              </div>
              {opponent.bet > 0 && (
                <div className="opponent-bet">
                  $<CountUp value={opponent.bet} from={0} />
                </div>
              )}
              {/* Revealed hole cards during run-it-out showdown */}
              {revealedCards?.players_cards[opponent.name] && (
                <div
                  className="opponent-revealed-cards"
                  style={{ '--reveal-index': revealOrder.get(opponent.name) ?? 0 } as CSSProperties}
                >
                  {revealedCards.players_cards[opponent.name].map((card, i) => (
                    <Card key={i} card={card} faceDown={false} size="large" />
                  ))}
                </div>
              )}
              <ActionBadge
                player={opponent}
                lastKnownActions={lastKnownActions}
                onFadeComplete={onFadeComplete}
              />
            </div>
          );
        })}

        {/* Heads-up psychology panel */}
        {isHeadsUp && headsUpOpponent && providedGameId && (
          <HeadsUpOpponentPanel
            opponent={headsUpOpponent}
            gameId={providedGameId}
            humanPlayerName={humanPlayerName}
          />
        )}
      </div>
    </div>
  );
}
