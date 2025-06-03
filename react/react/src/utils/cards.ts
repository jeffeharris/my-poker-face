// Playing card utilities and deck generation

export interface Card {
  suit: 'hearts' | 'diamonds' | 'clubs' | 'spades';
  rank: 'A' | '2' | '3' | '4' | '5' | '6' | '7' | '8' | '9' | '10' | 'J' | 'Q' | 'K';
  value: number; // For poker hand evaluation
  unicode: string; // Unicode symbol
  color: 'red' | 'black';
}

// Unicode playing card symbols (complete deck)
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

// Text-based card representations (fallback)
const TEXT_SYMBOLS = {
  spades: '♠',
  hearts: '♥', 
  diamonds: '♦',
  clubs: '♣'
};

export function createDeck(): Card[] {
  const suits: Array<keyof typeof CARD_SYMBOLS> = ['spades', 'hearts', 'diamonds', 'clubs'];
  const ranks: Array<keyof typeof CARD_SYMBOLS.spades> = ['A', '2', '3', '4', '5', '6', '7', '8', '9', '10', 'J', 'Q', 'K'];
  const deck: Card[] = [];

  suits.forEach(suit => {
    ranks.forEach(rank => {
      deck.push({
        suit,
        rank,
        value: getRankValue(rank),
        unicode: CARD_SYMBOLS[suit][rank],
        color: suit === 'hearts' || suit === 'diamonds' ? 'red' : 'black'
      });
    });
  });

  return deck;
}

export function getRankValue(rank: string): number {
  switch (rank) {
    case 'A': return 14; // Ace high
    case 'K': return 13;
    case 'Q': return 12;
    case 'J': return 11;
    default: return parseInt(rank);
  }
}

export function shuffleDeck(deck: Card[]): Card[] {
  const shuffled = [...deck];
  for (let i = shuffled.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [shuffled[i], shuffled[j]] = [shuffled[j], shuffled[i]];
  }
  return shuffled;
}

// Parse card string like "A♠" or "10♥" to Card object
export function parseCard(cardString: string): Card | null {
  if (!cardString) return null;
  
  // Extract rank and suit from string like "A♠"
  const suitSymbol = cardString.slice(-1);
  const rank = cardString.slice(0, -1);
  
  let suit: keyof typeof CARD_SYMBOLS;
  switch (suitSymbol) {
    case '♠': suit = 'spades'; break;
    case '♥': suit = 'hearts'; break;
    case '♦': suit = 'diamonds'; break;
    case '♣': suit = 'clubs'; break;
    default: return null;
  }
  
  if (!CARD_SYMBOLS[suit][rank as keyof typeof CARD_SYMBOLS.spades]) {
    return null;
  }
  
  return {
    suit,
    rank: rank as any,
    value: getRankValue(rank),
    unicode: CARD_SYMBOLS[suit][rank as keyof typeof CARD_SYMBOLS.spades],
    color: suit === 'hearts' || suit === 'diamonds' ? 'red' : 'black'
  };
}

// Convert Card object back to string format like "A♠"
export function cardToString(card: Card): string {
  return `${card.rank}${TEXT_SYMBOLS[card.suit]}`;
}

// Get a random card from deck
export function drawCard(deck: Card[]): { card: Card | null, remainingDeck: Card[] } {
  if (deck.length === 0) return { card: null, remainingDeck: [] };
  
  const card = deck[0];
  const remainingDeck = deck.slice(1);
  return { card, remainingDeck };
}

// Get multiple cards from deck
export function drawCards(deck: Card[], count: number): { cards: Card[], remainingDeck: Card[] } {
  const cards = deck.slice(0, count);
  const remainingDeck = deck.slice(count);
  return { cards, remainingDeck };
}