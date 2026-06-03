import { FastForward, Check, X } from 'lucide-react';
import { Card } from '../../cards';
import { ActionButtons } from '../ActionButtons';
import { useCardAnimation } from '../../../hooks/useCardAnimation';
import { heroCardAnimation } from '../../mobile/heroCardAnimation';
import type { Player } from '../../../types/player';
import type { BettingContext } from '../../../types/game';
import type { QueuedAction } from '../../../hooks/usePokerGame';
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
  bettingContext?: BettingContext;
  /** True when the backend is resolving the rest of the orbit via the
   *  no-LLM tiered path. Auto-clears when action returns to the human. */
  fastForward?: boolean;
  /** Called when the user taps the FF button. Receives the *new* desired
   *  state (toggle of `fastForward`). Parent POSTs to
   *  /api/game/<id>/fast-forward with `{enabled}`. Omit to hide. */
  onFastForward?: (enabled: boolean) => void;
  /** When true, every AI resolves with zero LLM calls (Solver + AI Chat off),
   *  so there's nothing to fast-forward — the FF button is hidden. */
  aiInstant?: boolean;
  /** When true, game speed is 'always' — FF is permanently on, so hide the
   *  (now redundant) FF button. */
  alwaysFastForward?: boolean;
  /** True while an AI is deliberating (someone else is acting). Enables the
   *  preemptive check/fold control. */
  aiThinking?: boolean;
  /** True when the player currently to act is an AI (not the human). Gates the
   *  preemptive control so it only shows while waiting on a bot. */
  currentPlayerIsAI?: boolean;
  /** Currently queued auto-action (e.g. 'check_fold'), or null. */
  queuedAction?: QueuedAction;
  /** Toggle the queued check/fold. Omit to hide the preemptive control. */
  onQueueCheckFold?: () => void;
  /** Coach-recommended action to highlight on the matching button. */
  recommendedAction?: string | null;
  /** Coach-suggested raise amount to pre-fill the raise slider. */
  raiseToAmount?: number | null;
  /** True from the all-in matchup reveal until the run-out board starts dealing.
   *  Animates the hero's hole cards lifting to "present" (show your hand). */
  heroCommitted?: boolean;
  /** True while the run-out board is dealing — pulls the presented cards back
   *  down so the community cards have clear visual space. */
  heroRetreating?: boolean;
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
  bettingContext,
  fastForward = false,
  onFastForward,
  aiInstant = false,
  alwaysFastForward = false,
  aiThinking = false,
  currentPlayerIsAI = false,
  queuedAction = null,
  onQueueCheckFold,
  recommendedAction,
  raiseToAmount,
  heroCommitted = false,
  heroRetreating = false,
}: PlayerCommandCenterProps) {
  const costToCall = Math.max(0, highestBet - player.bet);

  const {
    displayCards,
    cardTransforms,
    isDealing,
    isExiting,
    cardsNeat,
    toggleCardsNeat,
    handleExitAnimationEnd,
  } = useCardAnimation({ hand: player.hand });

  // Preemptive check/fold: while a bot is deliberating and we're still live,
  // let the player pre-queue the no-cost defensive action (mirrors mobile).
  const showPreemptive =
    !!onQueueCheckFold &&
    !showActions &&
    !isCurrentPlayer &&
    aiThinking &&
    currentPlayerIsAI &&
    !player.is_folded;

  return (
    <div
      className={`player-command-center ${
        isCurrentPlayer ? 'is-active' : ''
      } ${player.is_folded ? 'is-folded' : ''} ${player.is_all_in ? 'is-all-in' : ''}`}
    >
      {/* Bet pill - positioned at top, overlapping border */}
      {player.bet > 0 && <div className="command-center__bet-pill">Bet: ${player.bet}</div>}

      {/* Top section: Cards + Info */}
      <div className="command-center__top">
        {/* Player cards (larger display, animated deal-in + exit) */}
        <div
          className="command-center__cards"
          style={{
            gap: `${cardTransforms.gap}px`,
            transition: cardsNeat ? 'gap 0.2s ease-out' : 'none',
          }}
        >
          {isExiting && displayCards?.[0] && displayCards?.[1] ? (
            /* Exit animation: left card converges onto right, both sweep off */
            <>
              <div
                className="command-center__card-wrapper"
                style={
                  {
                    animation: 'cmdDealCardOut1 0.45s cubic-bezier(0.4, 0, 1, 1) forwards',
                    '--exit-start-x': `${cardTransforms.card1.offsetX}px`,
                    '--exit-start-y': `${cardTransforms.card1.offsetY}px`,
                    '--exit-start-rotation': `${cardTransforms.card1.rotation}deg`,
                    '--exit-converge-x': `${cardTransforms.card2.offsetX + cardTransforms.gap}px`,
                  } as React.CSSProperties
                }
              >
                <Card
                  card={displayCards[0]}
                  faceDown={false}
                  size="xlarge"
                  className="command-card"
                />
              </div>
              <div
                className="command-center__card-wrapper"
                onAnimationEnd={handleExitAnimationEnd}
                style={
                  {
                    animation: 'cmdDealCardOut2 0.45s cubic-bezier(0.4, 0, 1, 1) forwards',
                    '--exit-start-x': `${cardTransforms.card2.offsetX}px`,
                    '--exit-start-y': `${cardTransforms.card2.offsetY}px`,
                    '--exit-start-rotation': `${cardTransforms.card2.rotation}deg`,
                  } as React.CSSProperties
                }
              >
                <Card
                  card={displayCards[1]}
                  faceDown={false}
                  size="xlarge"
                  className="command-card"
                />
              </div>
            </>
          ) : displayCards?.[0] && displayCards?.[1] ? (
            /* Normal display with deal-in / runout commit / retreat animation.
               heroCardAnimation() returns the right CSS `animation` shorthand for
               all three states: present (throw up), retreat (pull down), deal-in.
               It degrades to 'none' when none of those states are active, which
               leaves the inline `transform` prop in control — matching the prior
               behavior exactly for non-runout hands. */
            <>
              <div
                className={`command-center__card-wrapper${heroCommitted ? ' cmd-hero-committed' : ''}`}
                onClick={toggleCardsNeat}
                style={
                  {
                    transform: `rotate(${cardTransforms.card1.rotation}deg) translateX(${cardTransforms.card1.offsetX}px) translateY(${cardTransforms.card1.offsetY}px)`,
                    transition: cardsNeat ? 'transform 0.2s ease-out' : 'none',
                    cursor: 'pointer',
                    animation:
                      heroCommitted || heroRetreating
                        ? heroCardAnimation(
                            'Left',
                            { heroRetreating, heroCommitted, isDealing: false },
                            'cmd'
                          )
                        : isDealing
                          ? 'cmdDealCardIn 0.55s cubic-bezier(0.16, 1, 0.3, 1) both'
                          : 'none',
                    opacity: player.is_folded ? 0.5 : 1,
                    '--deal-rotation': `${cardTransforms.card1.rotation}deg`,
                    '--deal-start-rotation': `${cardTransforms.card1.startRotation}deg`,
                    '--deal-offset-x': `${cardTransforms.card1.offsetX}px`,
                    '--deal-offset-y': `${cardTransforms.card1.offsetY}px`,
                  } as React.CSSProperties
                }
              >
                <Card
                  card={displayCards[0]}
                  faceDown={false}
                  size="xlarge"
                  className="command-card"
                />
              </div>
              <div
                className={`command-center__card-wrapper${heroCommitted ? ' cmd-hero-committed' : ''}`}
                onClick={toggleCardsNeat}
                style={
                  {
                    transform: `rotate(${cardTransforms.card2.rotation}deg) translateX(${cardTransforms.card2.offsetX}px) translateY(${cardTransforms.card2.offsetY}px)`,
                    transition: cardsNeat ? 'transform 0.2s ease-out' : 'none',
                    cursor: 'pointer',
                    animation:
                      heroCommitted || heroRetreating
                        ? heroCardAnimation(
                            'Right',
                            { heroRetreating, heroCommitted, isDealing: false },
                            'cmd'
                          )
                        : isDealing
                          ? 'cmdDealCardIn 0.55s cubic-bezier(0.16, 1, 0.3, 1) 0.15s both'
                          : 'none',
                    opacity: player.is_folded ? 0.5 : 1,
                    '--deal-rotation': `${cardTransforms.card2.rotation}deg`,
                    '--deal-start-rotation': `${cardTransforms.card2.startRotation}deg`,
                    '--deal-offset-x': `${cardTransforms.card2.offsetX}px`,
                    '--deal-offset-y': `${cardTransforms.card2.offsetY}px`,
                  } as React.CSSProperties
                }
              >
                <Card
                  card={displayCards[1]}
                  faceDown={false}
                  size="xlarge"
                  className="command-card"
                />
              </div>
            </>
          ) : (
            /* No cards yet — show placeholders */
            <>
              <div className="command-card placeholder" />
              <div className="command-card placeholder" />
            </>
          )}
        </div>

        {/* Player info */}
        <div className="command-center__info">
          <div className="command-center__details">
            <div className="command-center__name">{player.name}</div>
            <div className="command-center__stack">
              <span className="stack-value">${player.stack.toLocaleString()}</span>
            </div>
            {costToCall > 0 && !player.is_folded && (
              <div className="command-center__to-call">
                To call: <span className="to-call-amount">${costToCall}</span>
              </div>
            )}
          </div>
        </div>

        {/* Position indicators */}
        <div className="command-center__positions">
          {isDealer && (
            <div className="position-chip dealer" title="Dealer">
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
            bettingContext={bettingContext}
            recommendedAction={recommendedAction}
            raiseToAmount={raiseToAmount}
          />
        </div>
      )}

      {/* Preemptive check/fold — pre-queue the defensive action while a bot
          is still deliberating, so the hand snaps forward when it reaches us. */}
      {showPreemptive && (
        <div className="command-center__preemptive">
          <button
            type="button"
            className={`command-center__preemptive-button ${
              queuedAction === 'check_fold' ? 'is-queued' : ''
            }`}
            data-testid="action-btn-preemptive"
            onClick={onQueueCheckFold}
            title="Automatically check if you can, otherwise fold, when action reaches you"
          >
            {queuedAction === 'check_fold' ? (
              <>
                <Check size={14} strokeWidth={2.25} aria-hidden />
                <span>Check/Fold queued · tap to cancel</span>
              </>
            ) : (
              <>
                <span className="preemptive-icons" aria-hidden>
                  <Check size={14} strokeWidth={2.25} />
                  <X size={14} strokeWidth={2.25} />
                </span>
                <span>Check/Fold</span>
              </>
            )}
          </button>
        </div>
      )}

      {/* Fast-forward: visible whenever it's NOT our turn — including
          while folded, since waiting out the orbit is exactly when FF
          matters most. Tap to toggle; the auto-reset also fires when
          action returns to the human on the next street/hand. */}
      {!showActions && !isCurrentPlayer && onFastForward && !aiInstant && !alwaysFastForward && (
        <div className="command-center__ff">
          <button
            type="button"
            className={`command-center__ff-button ${fastForward ? 'is-active' : ''}`}
            onClick={() => onFastForward(!fastForward)}
            title={
              fastForward
                ? 'Tap to return to normal speed'
                : 'Skip AI deliberation — resolve to your next turn'
            }
          >
            <FastForward size={14} strokeWidth={2.25} aria-hidden />
            <span>{fastForward ? 'Fast-forwarding · tap to stop' : 'Fast-forward'}</span>
          </button>
        </div>
      )}
    </div>
  );
}
