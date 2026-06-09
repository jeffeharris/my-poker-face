/**
 * MobileCommunityCards — the five community-card slots with their cascade
 * deal-in animation. Extracted verbatim from MobilePokerTable; placeholders
 * fade out as each card arrives, the card overlays with a per-slot delay.
 */

import { Card } from '../cards';

interface CommunityCardAnim {
  shouldAnimate: boolean;
  delay: number;
  duration: number;
}

export function MobileCommunityCards({
  communityCards,
  animations,
  elevated = false,
}: {
  communityCards: string[];
  animations: CommunityCardAnim[];
  /** Lift above the chat while the cards slide in (non-run-out dealing), so the
   *  deal is visible over table talk; drops back below once settled. */
  elevated?: boolean;
}) {
  return (
    <div
      className={`mobile-community${elevated ? ' mobile-community--dealing' : ''}`}
      data-testid="mobile-community"
    >
      <div className="community-cards-row">
        {Array.from({ length: 5 }).map((_, i) => {
          const card = communityCards[i];
          const anim = animations[i];
          const isDealt = !!card;
          const isAnimating = anim?.shouldAnimate;
          return (
            <div key={i} className="community-card-slot">
              {/* Placeholder fades out when card arrives */}
              <div
                className={`community-card-placeholder ${isDealt ? (isAnimating ? 'fade-out-delayed' : 'hidden') : ''}`}
                style={
                  isAnimating
                    ? { animationDelay: `${anim.delay + anim.duration * 0.6}s` }
                    : undefined
                }
              />
              {/* Card overlays placeholder */}
              {isDealt && (
                <div
                  className="community-card-overlay"
                  style={
                    isAnimating
                      ? {
                          animation: `communityCardDealIn ${anim.duration}s cubic-bezier(0.16, 1, 0.3, 1) ${anim.delay}s both`,
                        }
                      : undefined
                  }
                >
                  <Card card={card} faceDown={false} size="medium" />
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
