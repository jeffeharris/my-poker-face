/**
 * Utility for converting numeric seeds to memorable adjective-noun pairs.
 *
 * Examples: "swift-tiger", "calm-river", "bold-falcon"
 */

const ADJECTIVES = [
  'swift', 'calm', 'bold', 'bright', 'clever', 'daring', 'eager', 'fancy',
  'gentle', 'happy', 'jolly', 'keen', 'lively', 'merry', 'noble', 'proud',
  'quick', 'royal', 'shiny', 'brave', 'cosmic', 'dusty', 'electric', 'fierce',
  'golden', 'hidden', 'icy', 'jade', 'kindly', 'lucky', 'mystic', 'neon',
  'orange', 'purple', 'quiet', 'rusty', 'silver', 'turbo', 'ultra', 'velvet',
  'wild', 'xenon', 'yellow', 'zesty', 'ancient', 'blazing', 'crystal', 'dark',
  'epic', 'frozen', 'giant', 'hollow', 'iron', 'jumbo', 'krypton', 'laser',
  'mega', 'ninja', 'omega', 'plasma', 'quantum', 'rapid', 'stellar', 'thunder',
  'vapor', 'warp', 'atomic', 'binary', 'chrome', 'delta', 'ember', 'flash',
  'gamma', 'hyper', 'infra', 'joker', 'karma', 'lunar', 'matrix', 'nova',
  'orbit', 'pixel', 'quasar', 'radar', 'solar', 'titan', 'ultra', 'vector',
  'winter', 'xerox', 'yonder', 'zenith', 'alpha', 'beta', 'cyber', 'dusk',
];

const NOUNS = [
  'tiger', 'river', 'falcon', 'storm', 'flame', 'frost', 'shadow', 'light',
  'thunder', 'wolf', 'dragon', 'phoenix', 'eagle', 'shark', 'lion', 'bear',
  'hawk', 'viper', 'cobra', 'panther', 'jaguar', 'raven', 'crow', 'owl',
  'fox', 'deer', 'horse', 'whale', 'dolphin', 'otter', 'badger', 'lynx',
  'comet', 'meteor', 'planet', 'galaxy', 'nebula', 'quasar', 'pulsar', 'star',
  'moon', 'sun', 'orbit', 'void', 'spark', 'blaze', 'ember', 'flare',
  'wave', 'tide', 'reef', 'coral', 'pearl', 'shell', 'sand', 'stone',
  'crystal', 'diamond', 'ruby', 'emerald', 'jade', 'onyx', 'opal', 'amber',
  'forest', 'jungle', 'desert', 'tundra', 'canyon', 'valley', 'peak', 'ridge',
  'stream', 'lake', 'ocean', 'glacier', 'volcano', 'island', 'cliff', 'cave',
  'breeze', 'gust', 'gale', 'cyclone', 'tornado', 'tempest', 'squall', 'zephyr',
  'dawn', 'dusk', 'night', 'day', 'spring', 'summer', 'autumn', 'winter',
];

/**
 * Convert a numeric seed to an adjective-noun pair.
 * Uses modular arithmetic to map any positive integer to a word pair.
 */
export function seedToWords(seed: number): string {
  const adjIndex = seed % ADJECTIVES.length;
  const nounIndex = Math.floor(seed / ADJECTIVES.length) % NOUNS.length;
  return `${ADJECTIVES[adjIndex]}-${NOUNS[nounIndex]}`;
}

/**
 * Convert an adjective-noun pair back to a numeric seed.
 * Returns null if the words aren't recognized.
 */
export function wordsToSeed(words: string): number | null {
  const parts = words.toLowerCase().split('-');
  if (parts.length !== 2) return null;

  const adjIndex = ADJECTIVES.indexOf(parts[0]);
  const nounIndex = NOUNS.indexOf(parts[1]);

  if (adjIndex === -1 || nounIndex === -1) return null;

  return adjIndex + (nounIndex * ADJECTIVES.length);
}

/**
 * Generate a new random seed.
 */
export function generateSeed(): number {
  // Generate a seed that maps to our word space
  const maxSeed = ADJECTIVES.length * NOUNS.length;
  return Math.floor(Math.random() * maxSeed);
}

/**
 * Check if a string looks like a word-based seed (adjective-noun format).
 */
export function isWordSeed(value: string): boolean {
  return /^[a-z]+-[a-z]+$/i.test(value);
}
