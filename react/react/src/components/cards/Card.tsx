import { type Card as CardType, parseCard, cardFromBackend } from '../../utils/cards';
import './Card.css';

// Backend card format (from Python API)
interface BackendCard {
  rank: string;
  suit: string;
}

interface CardProps {
  card?: CardType | BackendCard | string | null;
  faceDown?: boolean;
  size?: 'xsmall' | 'small' | 'medium' | 'large' | 'xlarge';
  className?: string;
}

export function Card({ card, faceDown = false, size = 'medium', className = '' }: CardProps) {
  // Handle different card input types
  let cardObj: CardType | null = null;

  if (typeof card === 'string') {
    // First try to parse as a stringified dict (e.g., "{'rank': '5', 'suit': 'Clubs'}")
    // This can happen when str(dict) is called instead of str(Card)
    if (card.startsWith('{') && card.includes('rank') && card.includes('suit')) {
      try {
        // Convert Python-style string to JSON (single quotes to double quotes)
        const jsonStr = card.replace(/'/g, '"');
        const parsed = JSON.parse(jsonStr);
        if (parsed.rank && parsed.suit) {
          cardObj = cardFromBackend(parsed);
        }
      } catch (error) {
        // Log error but still fall through to parseCard
        console.error('Failed to parse Python-style card string in Card component:', {
          cardString: card,
          error,
        });
      }
    }
    // If stringified dict parsing didn't work, try normal card string parsing (e.g., "J♥")
    if (!cardObj) {
      cardObj = parseCard(card);
    }
  } else if (card && typeof card === 'object') {
    // Check if it's a backend card object with rank and suit properties
    if ('rank' in card && 'suit' in card && !('unicode' in card)) {
      // Convert backend format to our card format
      cardObj = cardFromBackend(card as { rank: string; suit: string });
    } else {
      // It's already a CardType object
      cardObj = card as CardType;
    }
  }

  if (faceDown || !cardObj) {
    if (!faceDown && !cardObj && card) {
      console.error('[Card] Rendering card-back because cardObj is null. Original card:', card);
    }
    return (
      <div className={`playing-card card-back ${size} ${className}`}>
        <div className="card-back-pattern">🂠</div>
      </div>
    );
  }

  return (
    <div className={`playing-card image-card ${size} ${className}`}>
      <img src={cardObj.imagePath} alt={`${cardObj.rank} of ${cardObj.suit}`} className="card-image" />
    </div>
  );
}

// Specialized components
export function CommunityCard({ card, revealed = false }: { card?: CardType | BackendCard | string | null, revealed?: boolean }) {
  return <Card card={card} faceDown={!revealed} size="large" className="community-card" />;
}

export function HoleCard({ card, visible = false, size = 'large' }: { card?: CardType | BackendCard | string | null, visible?: boolean, size?: 'xsmall' | 'small' | 'medium' | 'large' | 'xlarge' }) {
  return <Card card={card} faceDown={!visible} size={size} className="hole-card" />;
}

export function DeckCard() {
  return <Card faceDown={true} size="medium" className="deck-card" />;
}
