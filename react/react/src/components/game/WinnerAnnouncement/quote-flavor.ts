// Between-hand flavor quotes. All content is original or common poker folk
// wisdom (idioms with no fixed author). Tag entries by mood and the moments
// they fit so callers can pick contextually.

export type QuoteMood =
  | 'cocky'
  | 'fatalistic'
  | 'philosophical'
  | 'funny'
  | 'menacing'
  | 'weary'
  | 'hopeful';

export type QuoteTrigger =
  | 'between_hands'
  | 'after_win'
  | 'after_loss'
  | 'bad_beat'
  | 'big_pot'
  | 'all_in'
  | 'bluff_succeeded'
  | 'bluff_caught';

export interface PokerQuote {
  text: string;
  attribution: string;
  mood: QuoteMood;
  triggers: QuoteTrigger[];
}

export const POKER_QUOTES: PokerQuote[] = [
  // ---------- Cocky ----------
  {
    text: 'I came here with chips and a smile. You can keep the smile.',
    attribution: '— The House',
    mood: 'cocky',
    triggers: ['between_hands', 'after_win'],
  },
  {
    text: 'Every hand you play against me is a tuition payment.',
    attribution: '— The House',
    mood: 'cocky',
    triggers: ['between_hands', 'after_win'],
  },
  {
    text: 'Read ’em and weep. I prefer the second part.',
    attribution: '— The House',
    mood: 'cocky',
    triggers: ['after_win', 'big_pot'],
  },
  {
    text: 'You sat down. That was your mistake. The rest is just procedure.',
    attribution: '— The House',
    mood: 'cocky',
    triggers: ['between_hands'],
  },
  {
    text: 'I don’t bluff. I just let you imagine things.',
    attribution: '— The House',
    mood: 'cocky',
    triggers: ['bluff_succeeded', 'between_hands'],
  },
  {
    text: 'The seat you’re in? I bought it. With chips that used to be yours.',
    attribution: '— The House',
    mood: 'cocky',
    triggers: ['after_win', 'big_pot'],
  },

  // ---------- Fatalistic ----------
  {
    text: 'The deck doesn’t owe me anything. It never has.',
    attribution: '— The Grizzled Pro',
    mood: 'fatalistic',
    triggers: ['after_loss', 'bad_beat', 'between_hands'],
  },
  {
    text: 'Win or lose, the felt swallows it all the same.',
    attribution: '— The Grizzled Pro',
    mood: 'fatalistic',
    triggers: ['between_hands', 'after_loss'],
  },
  {
    text: 'Aces look the same face-down as a seven-deuce. So does everything else here.',
    attribution: '— The Grizzled Pro',
    mood: 'fatalistic',
    triggers: ['between_hands'],
  },
  {
    text: 'The river doesn’t care which side you’re standing on.',
    attribution: '— The Grizzled Pro',
    mood: 'fatalistic',
    triggers: ['bad_beat', 'after_loss'],
  },
  {
    text: 'You don’t lose to the cards. You lose to the math, eventually.',
    attribution: '— The Grizzled Pro',
    mood: 'fatalistic',
    triggers: ['after_loss', 'between_hands'],
  },
  {
    text: 'Two pair holds up. Until it doesn’t.',
    attribution: '— Poker proverb',
    mood: 'fatalistic',
    triggers: ['bad_beat'],
  },

  // ---------- Philosophical ----------
  {
    text: 'Poker is a long argument with probability, and probability always has the last word.',
    attribution: '— The Old Sage',
    mood: 'philosophical',
    triggers: ['between_hands'],
  },
  {
    text: 'Every fold is a small grief. Every call, a small faith.',
    attribution: '— The Old Sage',
    mood: 'philosophical',
    triggers: ['between_hands'],
  },
  {
    text: 'You don’t play the cards. You play the table, and the table plays you back.',
    attribution: '— The Old Sage',
    mood: 'philosophical',
    triggers: ['between_hands'],
  },
  {
    text: 'There are two kinds of bad beats: the ones you caused, and the ones you blamed on the deck.',
    attribution: '— The Old Sage',
    mood: 'philosophical',
    triggers: ['bad_beat', 'after_loss'],
  },
  {
    text: 'The hand is dealt. The choice is yours. That’s the whole game.',
    attribution: '— The Old Sage',
    mood: 'philosophical',
    triggers: ['between_hands'],
  },
  {
    text: 'Position is just patience pretending to be strategy.',
    attribution: '— The Old Sage',
    mood: 'philosophical',
    triggers: ['between_hands'],
  },
  {
    text: 'The chips remember nothing. That’s why they’re honest.',
    attribution: '— The Old Sage',
    mood: 'philosophical',
    triggers: ['between_hands'],
  },

  // ---------- Funny ----------
  {
    text: 'My retirement plan is one good hand and a short memory.',
    attribution: '— The Degenerate',
    mood: 'funny',
    triggers: ['between_hands', 'after_loss'],
  },
  {
    text: 'Variance: a fancy word for “why are my chips smaller.”',
    attribution: '— The Math Nerd',
    mood: 'funny',
    triggers: ['after_loss', 'bad_beat'],
  },
  {
    text: 'I told my therapist I had a problem. She raised.',
    attribution: '— The Degenerate',
    mood: 'funny',
    triggers: ['between_hands'],
  },
  {
    text: 'I’m not on tilt. I’m on a brisk emotional incline.',
    attribution: '— The Degenerate',
    mood: 'funny',
    triggers: ['after_loss', 'bad_beat'],
  },
  {
    text: 'I came for the cards and stayed for the unresolved trauma.',
    attribution: '— The Degenerate',
    mood: 'funny',
    triggers: ['between_hands'],
  },
  {
    text: 'My strategy: small ball until it isn’t.',
    attribution: '— The Math Nerd',
    mood: 'funny',
    triggers: ['between_hands'],
  },
  {
    text: 'Pot odds said no. I said “definitely.” We compromised.',
    attribution: '— The Math Nerd',
    mood: 'funny',
    triggers: ['after_loss', 'between_hands'],
  },

  // ---------- Menacing ----------
  {
    text: 'When I look at my cards, I’m not deciding. I already know.',
    attribution: '— The Cowboy',
    mood: 'menacing',
    triggers: ['between_hands', 'big_pot'],
  },
  {
    text: 'There’s a hand coming where you’ll wish you’d folded an hour ago.',
    attribution: '— The Cowboy',
    mood: 'menacing',
    triggers: ['between_hands'],
  },
  {
    text: 'I let people win small pots. It keeps them at the table.',
    attribution: '— The Cowboy',
    mood: 'menacing',
    triggers: ['between_hands', 'after_loss'],
  },
  {
    text: 'You think you’re ahead. That’s how I know I am.',
    attribution: '— The Cowboy',
    mood: 'menacing',
    triggers: ['between_hands'],
  },
  {
    text: 'Quiet table. Quiet hands. Loud finish.',
    attribution: '— The Cowboy',
    mood: 'menacing',
    triggers: ['between_hands', 'all_in'],
  },

  // ---------- Weary ----------
  {
    text: 'You learn to lose before you learn to win. Most people stop at the lesson.',
    attribution: '— The Grizzled Pro',
    mood: 'weary',
    triggers: ['after_loss', 'between_hands'],
  },
  {
    text: 'I’ve been at this table longer than most marriages. It hasn’t loved me back either.',
    attribution: '— The Grizzled Pro',
    mood: 'weary',
    triggers: ['between_hands'],
  },
  {
    text: 'Some days the cards run. Some days you do.',
    attribution: '— The Grizzled Pro',
    mood: 'weary',
    triggers: ['after_loss', 'between_hands'],
  },
  {
    text: 'Every chip in this stack used to belong to somebody who needed it more.',
    attribution: '— The Grizzled Pro',
    mood: 'weary',
    triggers: ['big_pot', 'between_hands'],
  },
  {
    text: 'The lights don’t change. The seats don’t change. Only the names on the chips.',
    attribution: '— The Grizzled Pro',
    mood: 'weary',
    triggers: ['between_hands'],
  },

  // ---------- Hopeful ----------
  {
    text: 'The next card is always the one that changes everything. Until it doesn’t.',
    attribution: '— The Dreamer',
    mood: 'hopeful',
    triggers: ['between_hands', 'all_in'],
  },
  {
    text: 'You’re one hand away. You’re always one hand away.',
    attribution: '— The Dreamer',
    mood: 'hopeful',
    triggers: ['between_hands', 'after_loss'],
  },
  {
    text: 'Short stack, long memory, sharp teeth.',
    attribution: '— The Dreamer',
    mood: 'hopeful',
    triggers: ['after_loss', 'all_in'],
  },
  {
    text: 'A chip and a chair. That’s the whole legend.',
    attribution: '— Poker proverb',
    mood: 'hopeful',
    triggers: ['after_loss', 'all_in', 'between_hands'],
  },

  // ---------- Folk wisdom (uncopyrightable poker idioms) ----------
  {
    text: 'If you can’t spot the sucker at the table in the first half hour, you are the sucker.',
    attribution: '— Poker proverb',
    mood: 'philosophical',
    triggers: ['between_hands'],
  },
  {
    text: 'Trust everyone, but always cut the cards.',
    attribution: '— Poker proverb',
    mood: 'philosophical',
    triggers: ['between_hands'],
  },
  {
    text: 'The cards have no memory. Only the players do.',
    attribution: '— Poker proverb',
    mood: 'philosophical',
    triggers: ['between_hands', 'bad_beat'],
  },
  {
    text: 'Play the player, not the cards.',
    attribution: '— Poker proverb',
    mood: 'philosophical',
    triggers: ['between_hands'],
  },
  {
    text: 'The chips will tell you what kind of player you are. The seat will tell you how long you get to be one.',
    attribution: '— Poker proverb',
    mood: 'philosophical',
    triggers: ['between_hands'],
  },
  {
    text: 'Scared money never wins.',
    attribution: '— Poker proverb',
    mood: 'cocky',
    triggers: ['between_hands', 'all_in'],
  },
  {
    text: 'You can shear a sheep many times. You can skin him only once.',
    attribution: '— Poker proverb',
    mood: 'menacing',
    triggers: ['between_hands', 'after_win'],
  },
  {
    text: 'The best hand doesn’t always win. The best player usually does.',
    attribution: '— Poker proverb',
    mood: 'philosophical',
    triggers: ['between_hands'],
  },
  {
    text: 'Aces full of nothing still beat a story.',
    attribution: '— Poker proverb',
    mood: 'cocky',
    triggers: ['after_win', 'bluff_caught'],
  },

  // ---------- Bluff moments ----------
  {
    text: 'A good bluff costs nothing. A bad one costs everything.',
    attribution: '— Poker proverb',
    mood: 'philosophical',
    triggers: ['bluff_succeeded', 'bluff_caught', 'between_hands'],
  },
  {
    text: 'The hand I had isn’t the hand I showed.',
    attribution: '— The Cowboy',
    mood: 'menacing',
    triggers: ['bluff_succeeded'],
  },
  {
    text: 'You bought the story. You didn’t check the receipts.',
    attribution: '— The House',
    mood: 'cocky',
    triggers: ['bluff_succeeded'],
  },
  {
    text: 'Caught with my hand in the cookie jar. Cookies were stale anyway.',
    attribution: '— The Degenerate',
    mood: 'funny',
    triggers: ['bluff_caught'],
  },
  {
    text: 'They paid for the lesson. I gave them a discount.',
    attribution: '— The House',
    mood: 'cocky',
    triggers: ['bluff_caught', 'after_loss'],
  },

  // ---------- Bad beat ----------
  {
    text: 'Set over set is a tax. Pay it and move on.',
    attribution: '— The Grizzled Pro',
    mood: 'weary',
    triggers: ['bad_beat'],
  },
  {
    text: 'Two outs found me. They had my address.',
    attribution: '— The Degenerate',
    mood: 'funny',
    triggers: ['bad_beat'],
  },
  {
    text: 'I played it perfectly. The cards disagreed in writing.',
    attribution: '— The Math Nerd',
    mood: 'fatalistic',
    triggers: ['bad_beat'],
  },

  // ---------- Big pot / all-in ----------
  {
    text: 'All in. The shortest sentence in the language.',
    attribution: '— The Old Sage',
    mood: 'philosophical',
    triggers: ['all_in', 'big_pot'],
  },
  {
    text: 'Stack the chips. Don’t count them. Counting is for after.',
    attribution: '— The Cowboy',
    mood: 'menacing',
    triggers: ['big_pot', 'after_win'],
  },
  {
    text: 'Big pot, small breath.',
    attribution: '— Poker proverb',
    mood: 'philosophical',
    triggers: ['big_pot', 'all_in'],
  },
];

// ---- Selection helpers ----

export function pickRandomQuote<T>(arr: T[]): T {
  return arr[Math.floor(Math.random() * arr.length)];
}

export function quotesForTrigger(trigger: QuoteTrigger): PokerQuote[] {
  return POKER_QUOTES.filter((q) => q.triggers.includes(trigger));
}

export function quotesForMood(mood: QuoteMood): PokerQuote[] {
  return POKER_QUOTES.filter((q) => q.mood === mood);
}

export function pickQuote(trigger: QuoteTrigger, moodFilter?: QuoteMood[]): PokerQuote | undefined {
  let pool = quotesForTrigger(trigger);
  if (moodFilter && moodFilter.length > 0) {
    pool = pool.filter((q) => moodFilter.includes(q.mood));
  }
  if (pool.length === 0) return undefined;
  return pickRandomQuote(pool);
}
