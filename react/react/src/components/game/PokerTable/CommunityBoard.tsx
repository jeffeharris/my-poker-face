/**
 * CommunityBoard — the pot readout and the five community-card slots with their
 * flop-cascade / turn-river deal-in animation. Extracted from PokerTable's
 * `renderTableCore`. Empty slots render face-down placeholders.
 */

import { CommunityCard } from '../../cards';
import { CountUp } from '../../shared/CountUp';

interface CommunityCardAnim {
  shouldAnimate: boolean;
  delay: number;
  duration: number;
}

export function CommunityBoard({
  potTotal,
  communityCards,
  animations,
}: {
  potTotal: number;
  communityCards: string[];
  animations: CommunityCardAnim[];
}) {
  return (
    <div className="community-area">
      <div className="pot-area">
        <div className="pot">
          <div className="pot-label">POT</div>
          <div className="pot-amount">
            $<CountUp value={potTotal} />
          </div>
        </div>
      </div>

      <div className="community-cards">
        {Array.from({ length: 5 }).map((_, i) => {
          const card = communityCards[i];
          const anim = animations[i];
          const isAnimating = !!card && anim?.shouldAnimate;
          if (!card) {
            return <CommunityCard key={`placeholder-${i}`} revealed={false} />;
          }
          return (
            <div
              key={i}
              className="community-card-anim"
              style={
                isAnimating
                  ? {
                      animation: `communityCardDealIn ${anim.duration}s cubic-bezier(0.16, 1, 0.3, 1) ${anim.delay}s both`,
                    }
                  : undefined
              }
            >
              <CommunityCard card={card} revealed={true} />
            </div>
          );
        })}
      </div>
    </div>
  );
}
