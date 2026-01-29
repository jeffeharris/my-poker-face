export interface DeckPack {
  id: string;
  name: string;
  description: string;
  format: 'png' | 'svg';
  license: string;
  attribution?: string;
}

export const DECK_PACKS: DeckPack[] = [
  {
    id: 'classic',
    name: 'Classic',
    description: 'Clean, modern card designs',
    format: 'png',
    license: 'Included with game',
  },
  {
    id: 'standard',
    name: 'Standard',
    description: 'Borderless French pattern',
    format: 'svg',
    license: 'MIT',
    attribution: 'hayeah/playing-cards-assets',
  },
  {
    id: 'english',
    name: 'English',
    description: 'Traditional English pattern',
    format: 'svg',
    license: 'CC0 (Public Domain)',
    attribution: 'Wikimedia Commons',
  },
];
