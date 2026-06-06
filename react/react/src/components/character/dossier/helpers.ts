/**
 * Pure presentational helpers for the dossier card — deterministic id/monogram
 * derivation and the Renown-v2 badge styling map. Extracted from
 * CharacterDetailCard.tsx; no React, no side effects.
 */

export function deriveFileNumber(name: string): string {
  // Deterministic "looks like a real case file" id from the name.
  let h = 0;
  for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) >>> 0;
  const block = String.fromCharCode(65 + (h % 26));
  const digits = String(1000 + (h % 8999)).padStart(4, '0');
  return `${block}-${digits}`;
}

export function monogram(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return '?';
  if (parts.length === 1) return parts[0]!.slice(0, 2).toUpperCase();
  return (parts[0]![0]! + parts[parts.length - 1]![0]!).toUpperCase();
}

/**
 * Human-readable labels for the backend's internal avatar-emotion ids
 * (snake_case, from poker/character_images.py::EMOTIONS). The wax-seal badge
 * uppercases the text via CSS, so these are title-cased mainly for the
 * tooltip / aria copy — the real fix is never surfacing the raw `poker_face`
 * id with its underscore.
 */
const EMOTION_LABELS: Record<string, string> = {
  confident: 'Confident',
  happy: 'Happy',
  thinking: 'Thinking',
  nervous: 'Nervous',
  angry: 'Angry',
  shocked: 'Shocked',
  smug: 'Smug',
  frustrated: 'Frustrated',
  elated: 'Elated',
  gleeful: 'Gleeful',
  giddy: 'Giddy',
  sheepish: 'Sheepish',
  poker_face: 'Poker Face',
};

/**
 * Turn an internal emotion id into a display label. Known ids use the curated
 * map; anything else (a new backend emotion, a runtime state like "neutral")
 * falls back to a de-underscored, title-cased rendering so a raw snake_case
 * value never leaks into the badge.
 */
export function formatEmotion(emotion: string): string {
  const key = emotion.trim().toLowerCase();
  if (EMOTION_LABELS[key]) return EMOTION_LABELS[key];
  return key
    .split(/[_\s]+/)
    .filter(Boolean)
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(' ');
}

/** Map a Renown-v2 quadrant to a badge glyph + modifier class. Unknown
 *  quadrants fall back to the neutral "up-and-comer" treatment. */
export function renownBadgeStyle(quadrant: string): { glyph: string; mod: string } {
  switch (quadrant) {
    case 'Beloved Legend':
      return { glyph: '★', mod: 'legend' };
    case 'Infamous Villain':
      return { glyph: '☠', mod: 'villain' };
    case 'Disliked Nobody':
      return { glyph: '·', mod: 'nobody' };
    default: // "Up-and-comer"
      return { glyph: '↗', mod: 'comer' };
  }
}
