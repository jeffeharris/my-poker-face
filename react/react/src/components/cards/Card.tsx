import { type Card as CardType, parseCard } from '../../utils/cards';
import './Card.css';

interface CardProps {
  card?: CardType | string | null;
  faceDown?: boolean;
  size?: 'small' | 'medium' | 'large';
  className?: string;
}

export function Card({ card, faceDown = false, size = 'medium', className = '' }: CardProps) {
  // Handle different card input types
  let cardObj: CardType | null = null;
  
  if (typeof card === 'string') {
    cardObj = parseCard(card);
  } else if (card && typeof card === 'object') {
    // Check if it's a backend card object with rank and suit properties
    if ('rank' in card && 'suit' in card) {
      // Convert backend format to our card format
      const backendCard = card as { rank: string; suit: string };
      const suitMap: Record<string, 'hearts' | 'diamonds' | 'clubs' | 'spades'> = {
        'Hearts': 'hearts',
        'Diamonds': 'diamonds', 
        'Clubs': 'clubs',
        'Spades': 'spades'
      };
      
      const suit = suitMap[backendCard.suit];
      if (suit) {
        // Get unicode symbol from CARD_SYMBOLS
        const CARD_SYMBOLS = {
          spades: {
            'A': '🂡', '2': '🂢', '3': '🂣', '4': '🂤', '5': '🂥', '6': '🂦', '7': '🂧', '8': '🂨', 
            '9': '🂩', '10': '🂪', 'J': '🂫', 'Q': '🂭', 'K': '🂮'
          },
          hearts: {
            'A': '🂱', '2': '🂲', '3': '🂳', '4': '🂴', '5': '🂵', '6': '🂶', '7': '🂷', '8': '🂸',
            '9': '🂹', '10': '🂺', 'J': '🂻', 'Q': '🂽', 'K': '🂾'
          },
          diamonds: {
            'A': '🃁', '2': '🃂', '3': '🃃', '4': '🃄', '5': '🃅', '6': '🃆', '7': '🃇', '8': '🃈',
            '9': '🃉', '10': '🃊', 'J': '🃋', 'Q': '🃍', 'K': '🃎'
          },
          clubs: {
            'A': '🃑', '2': '🃒', '3': '🃓', '4': '🃔', '5': '🃕', '6': '🃖', '7': '🃗', '8': '🃘',
            '9': '🃙', '10': '🃚', 'J': '🃛', 'Q': '🃝', 'K': '🃞'
          }
        };
        
        const unicode = CARD_SYMBOLS[suit]?.[backendCard.rank as keyof typeof CARD_SYMBOLS.spades];
        if (unicode) {
          cardObj = {
            suit,
            rank: backendCard.rank as any,
            value: 0, // Not needed for display
            unicode,
            color: suit === 'hearts' || suit === 'diamonds' ? 'red' : 'black'
          };
        }
      }
    } else {
      // It's already a CardType object
      cardObj = card as CardType;
    }
  }

  if (faceDown || !cardObj) {
    return (
      <div className={`playing-card card-back ${size} ${className}`}>
        <div className="card-back-pattern">🂠</div>
      </div>
    );
  }

  return (
    <div className={`playing-card unicode-card ${cardObj.color} ${size} ${className}`}>
      {cardObj.unicode}
    </div>
  );
}

function getSuitSymbol(suit: string): string {
  switch (suit) {
    case 'spades': return '♠';
    case 'hearts': return '♥';
    case 'diamonds': return '♦';
    case 'clubs': return '♣';
    default: return '';
  }
}

// Specialized components
export function CommunityCard({ card, revealed = false }: { card?: CardType | string | null, revealed?: boolean }) {
  return <Card card={card} faceDown={!revealed} size="large" className="community-card" />;
}

export function HoleCard({ card, visible = false }: { card?: CardType | string | null, visible?: boolean }) {
  return <Card card={card} faceDown={!visible} size="small" className="hole-card" />;
}

export function DeckCard() {
  return <Card faceDown={true} size="medium" className="deck-card" />;
}