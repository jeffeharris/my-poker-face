/**
 * MobileHero — the hero (human) panel: cash button, dealer chip, name/stack,
 * bet chip, and the two hole cards with their deal-in / exit / run-out-commit
 * animations. Extracted verbatim from MobilePokerTable; all animation state is
 * computed by the card-animation + run-out directors and passed in.
 */

import type { CSSProperties } from 'react';
import type { Player } from '../../types/player';
import type { CardDealTransforms } from '../../types';
import type { CashModeInfo } from '../../types/game';
import { Card } from '../cards';
import { CountUp } from '../shared/CountUp';
import { MobileCashButton } from '../cash/MobileCashButton';
import { heroCardAnimation } from './heroCardAnimation';

export function MobileHero({
  humanPlayer,
  currentPlayerIsHuman,
  cashMode,
  onOpenCash,
  isHumanDealer,
  heroCommitted,
  heroRetreating,
  isExiting,
  isDealing,
  displayCards,
  cardTransforms,
  cardsNeat,
  toggleCardsNeat,
  onExitAnimationEnd,
}: {
  humanPlayer: Player | undefined;
  currentPlayerIsHuman: boolean;
  cashMode: CashModeInfo | null;
  onOpenCash: () => void;
  isHumanDealer: boolean;
  heroCommitted: boolean;
  heroRetreating: boolean;
  isExiting: boolean;
  isDealing: boolean;
  displayCards: Player['hand'] | null;
  cardTransforms: CardDealTransforms;
  cardsNeat: boolean;
  toggleCardsNeat: () => void;
  onExitAnimationEnd: () => void;
}) {
  return (
    <div
      className={[
        'mobile-hero',
        currentPlayerIsHuman && 'active-turn',
        humanPlayer?.is_folded && 'folded',
      ]
        .filter(Boolean)
        .join(' ')}
      data-testid="mobile-hero"
    >
      {/* Cash button - positioned in upper left of hero panel */}
      {cashMode && <MobileCashButton bankroll={cashMode.bankroll} onClick={onOpenCash} />}
      {/* Dealer chip - positioned in upper right */}
      {isHumanDealer && <span className="dealer-chip">D</span>}
      <div className="hero-info">
        <div className="hero-name">{humanPlayer?.name}</div>
        <div className="hero-stack">
          $<CountUp value={humanPlayer?.stack ?? 0} />
        </div>
      </div>
      {/* Bet chip - positioned at top edge of hero section */}
      {humanPlayer && humanPlayer.bet > 0 && (
        <div className="hero-bet">
          $<CountUp value={humanPlayer.bet} from={0} />
        </div>
      )}
      <div
        className={`hero-cards${heroCommitted ? ' hero-cards--committed' : ''}`}
        data-testid="hero-cards"
        style={{
          gap: `${cardTransforms.gap}px`,
          transition: cardsNeat ? 'gap 0.2s ease-out' : 'none',
        }}
      >
        {isExiting && displayCards?.[0] && displayCards?.[1] ? (
          /* Exit animation - cards sweep off, then onAnimationEnd triggers new cards */
          <>
            <div
              style={
                {
                  animation: `dealCardOut1 0.45s cubic-bezier(0.4, 0, 1, 1) forwards`,
                  '--exit-start-x': `${cardTransforms.card1.offsetX}px`,
                  '--exit-start-y': `${cardTransforms.card1.offsetY}px`,
                  '--exit-start-rotation': `${cardTransforms.card1.rotation}deg`,
                  '--exit-converge-x': `${cardTransforms.card2.offsetX + cardTransforms.gap}px`,
                } as CSSProperties
              }
            >
              <Card card={displayCards[0]} faceDown={false} size="xlarge" className="hero-card" />
            </div>
            <div
              onAnimationEnd={onExitAnimationEnd}
              style={
                {
                  animation: `dealCardOut2 0.45s cubic-bezier(0.4, 0, 1, 1) forwards`,
                  '--exit-start-x': `${cardTransforms.card2.offsetX}px`,
                  '--exit-start-y': `${cardTransforms.card2.offsetY}px`,
                  '--exit-start-rotation': `${cardTransforms.card2.rotation}deg`,
                } as CSSProperties
              }
            >
              <Card card={displayCards[1]} faceDown={false} size="xlarge" className="hero-card" />
            </div>
          </>
        ) : displayCards?.[0] && displayCards?.[1] ? (
          <>
            <div
              onClick={toggleCardsNeat}
              style={
                {
                  transform: `rotate(${cardTransforms.card1.rotation}deg) translateX(${cardTransforms.card1.offsetX}px) translateY(${cardTransforms.card1.offsetY}px)`,
                  transition: cardsNeat ? 'transform 0.2s ease-out' : 'none',
                  cursor: 'pointer',
                  // Run-out matchup: throw the left card up to present over
                  // the board and HOLD it there; pull it back down only once
                  // the run-out starts dealing (heroRetreating), so the board
                  // is clear. Same easing as the deal-in — reads smooth.
                  animation: heroCardAnimation('Left', {
                    heroRetreating,
                    heroCommitted,
                    isDealing,
                  }),
                  opacity: humanPlayer?.is_folded ? 0.5 : 1,
                  '--deal-rotation': `${cardTransforms.card1.rotation}deg`,
                  '--deal-start-rotation': `${cardTransforms.card1.startRotation}deg`,
                  '--deal-offset-x': `${cardTransforms.card1.offsetX}px`,
                  '--deal-offset-y': `${cardTransforms.card1.offsetY}px`,
                } as CSSProperties
              }
            >
              <Card card={displayCards[0]} faceDown={false} size="xlarge" className="hero-card" />
            </div>
            <div
              onClick={toggleCardsNeat}
              style={
                {
                  transform: `rotate(${cardTransforms.card2.rotation}deg) translateX(${cardTransforms.card2.offsetX}px) translateY(${cardTransforms.card2.offsetY}px)`,
                  transition: cardsNeat ? 'transform 0.2s ease-out' : 'none',
                  cursor: 'pointer',
                  // ...then, a beat later, the right card up beside it.
                  animation: heroCardAnimation('Right', {
                    heroRetreating,
                    heroCommitted,
                    isDealing,
                  }),
                  opacity: humanPlayer?.is_folded ? 0.5 : 1,
                  '--deal-rotation': `${cardTransforms.card2.rotation}deg`,
                  '--deal-start-rotation': `${cardTransforms.card2.startRotation}deg`,
                  '--deal-offset-x': `${cardTransforms.card2.offsetX}px`,
                  '--deal-offset-y': `${cardTransforms.card2.offsetY}px`,
                } as CSSProperties
              }
            >
              <Card card={displayCards[1]} faceDown={false} size="xlarge" className="hero-card" />
            </div>
          </>
        ) : (
          <>
            <div className="card-placeholder" />
            <div className="card-placeholder" />
          </>
        )}
      </div>
    </div>
  );
}
