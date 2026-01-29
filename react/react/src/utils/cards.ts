// Playing card utilities and deck generation

export interface Card {
  suit: 'hearts' | 'diamonds' | 'clubs' | 'spades';
  rank: 'A' | '2' | '3' | '4' | '5' | '6' | '7' | '8' | '9' | '10' | 'J' | 'Q' | 'K';
  value: number; // For poker hand evaluation
  unicode: string; // Unicode symbol
  imagePath: string; // Path to PNG image
  color: 'red' | 'black';
}

// Import card images for all packs
const classicImages = import.meta.glob('../assets/cards/classic/*.png', { eager: true, query: '?url', import: 'default' });
const standardImages = import.meta.glob('../assets/cards/standard/*.svg', { eager: true, query: '?url', import: 'default' });
const englishImages = import.meta.glob('../assets/cards/english/*.svg', { eager: true, query: '?url', import: 'default' });

const PACK_IMAGES: Record<string, Record<string, string>> = {
  classic: classicImages as Record<string, string>,
  standard: standardImages as Record<string, string>,
  english: englishImages as Record<string, string>,
};


const PACK_FORMATS: Record<string, string> = {
  classic: 'png',
  standard: 'svg',
  english: 'svg',
};

// Active pack (read from localStorage, updated by useDeckPack hook)
let _activePack = 'classic';
try {
  _activePack = localStorage.getItem('deckPack') || 'classic';
} catch { /* SSR or unavailable */ }

export function setActivePack(packId: string) {
  _activePack = packId;
}

export function getActivePack(): string {
  return _activePack;
}

// Generate image path for a card using the active pack
function getCardImagePath(rank: string, suit: string): string {
  return getCardImagePathForPack(rank, suit, _activePack);
}

// Generate image path for a card using a specific pack
export function getCardImagePathForPack(rank: string, suit: string, packId: string): string {
  const rankCode = rank === '10' ? 'T' : rank;
  const suitCode = suit.charAt(0).toUpperCase();
  const ext = PACK_FORMATS[packId] || 'png';
  const images = PACK_IMAGES[packId] || PACK_IMAGES.classic;
  const key = `../assets/cards/${packId}/${rankCode}${suitCode}.${ext}`;
  return images[key] || '';
}

// Unicode playing card symbols (complete deck)
export const CARD_SYMBOLS = {
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
        imagePath: getCardImagePath(rank, suit),
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
    rank: rank as Card['rank'],
    value: getRankValue(rank),
    unicode: CARD_SYMBOLS[suit][rank as keyof typeof CARD_SYMBOLS.spades],
    imagePath: getCardImagePath(rank, suit),
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

// Map backend suit names to internal suit keys
const SUIT_MAP: Record<string, 'hearts' | 'diamonds' | 'clubs' | 'spades'> = {
  'Hearts': 'hearts',
  'Diamonds': 'diamonds',
  'Clubs': 'clubs',
  'Spades': 'spades'
};

// Convert backend card format { rank: string, suit: string } to Card object
export function cardFromBackend(backendCard: { rank: string; suit: string }): Card | null {
  const suit = SUIT_MAP[backendCard.suit];
  if (!suit) return null;

  const unicode = CARD_SYMBOLS[suit]?.[backendCard.rank as keyof typeof CARD_SYMBOLS.spades];
  if (!unicode) return null;

  return {
    suit,
    rank: backendCard.rank as Card['rank'],
    value: getRankValue(backendCard.rank),
    unicode,
    imagePath: getCardImagePath(backendCard.rank, suit),
    color: suit === 'hearts' || suit === 'diamonds' ? 'red' : 'black'
  };
}