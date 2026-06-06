import { Check } from 'lucide-react';
import { useDeckPack } from '../../../../hooks/useDeckPack';
import { DECK_PACKS } from '../../../../hooks/deckPacks';
import { getCardImagePathForPack } from '../../../../utils/cards';
import './AppearanceSection.css';

const PREVIEW_ACES = ['spades', 'hearts', 'diamonds', 'clubs'] as const;
const PREVIEW_FACE_CARDS = [
  { rank: 'K', suit: 'spades' },
  { rank: 'Q', suit: 'spades' },
  { rank: 'J', suit: 'spades' },
  { rank: '10', suit: 'spades' },
];

/** Appearance settings — card deck pack picker. */
export function AppearanceSection() {
  const { activePackId, setPackId } = useDeckPack();

  return (
    <div className="us-appearance">
      <h3 className="us-appearance__title">Card Deck</h3>
      <p className="us-appearance__subtitle">Choose the visual style for your playing cards</p>

      <div className="us-appearance__packs">
        {DECK_PACKS.map((pack) => {
          const isActive = pack.id === activePackId;
          return (
            <button
              key={pack.id}
              type="button"
              className={`us-deck-pack ${isActive ? 'us-deck-pack--active' : ''}`}
              onClick={() => setPackId(pack.id)}
            >
              {/* Pack preview: stacked aces + face cards */}
              <div className="us-deck-pack__preview">
                {/* Stacked aces - spades on top */}
                <div className="us-deck-pack__aces">
                  {[...PREVIEW_ACES].reverse().map((suit, i) => {
                    const src = getCardImagePathForPack('A', suit, pack.id);
                    return (
                      <img
                        key={suit}
                        src={src}
                        alt={`Ace of ${suit}`}
                        className="us-deck-pack__ace-card"
                        style={{ zIndex: PREVIEW_ACES.length - i, marginLeft: i === 0 ? 0 : -28 }}
                      />
                    );
                  })}
                </div>
                {/* Face cards: K Q J 10 of spades */}
                {PREVIEW_FACE_CARDS.map(({ rank, suit }) => {
                  const src = getCardImagePathForPack(rank, suit, pack.id);
                  return (
                    <img
                      key={rank}
                      src={src}
                      alt={`${rank} of ${suit}`}
                      className="us-deck-pack__face-card"
                    />
                  );
                })}
              </div>

              {/* Pack info */}
              <div className="us-deck-pack__info">
                <span className="us-deck-pack__name">{pack.name}</span>
                <span className="us-deck-pack__desc">{pack.description}</span>
                {pack.attribution && <span className="us-deck-pack__license">{pack.license}</span>}
              </div>

              {/* Selected indicator */}
              {isActive && (
                <div className="us-deck-pack__check">
                  <Check size={16} />
                </div>
              )}
            </button>
          );
        })}
      </div>
    </div>
  );
}
