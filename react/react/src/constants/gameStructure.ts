/**
 * Single source of truth for tournament blind structures and the Custom Game
 * structure dropdown ranges.
 *
 * Both the tournament main menu (quick-start buttons) and Custom Game (preset
 * quick-picks + the advanced dropdowns) read from here, so the two can never
 * drift. Edit a preset once and it updates everywhere.
 */

export type BlindPresetId = 'quick' | 'tournament' | 'deep';

export interface BlindPreset {
  id: BlindPresetId;
  label: string;
  /** Short beats for the Custom Game preset cards — rendered one per row. */
  desc: string[];
  /** Longer hover/help copy shown in the tournament menu footer. */
  blurb: string;
  /** Starting depth in big blinds. */
  startingBB: number;
  /** Multiplier applied to the blinds at each increase. */
  blindGrowth: number;
  /** Hands between blind increases. */
  blindsIncrease: number;
  /** Hard cap on the big blind (0 = no cap). */
  maxBlind: number;
  /** Rough length estimate, minutes, with AI chat on (Faster mode is quicker). */
  estMinutes: number;
}

/** Base big blind used to turn a preset's BB depth into an absolute stack. */
export const PRESET_BIG_BLIND = 50;

export const BLIND_PRESETS: BlindPreset[] = [
  {
    id: 'quick',
    label: 'Quick & Dirty',
    desc: ['50 BB', 'fast blinds', '~10 min'],
    blurb: 'Quick & dirty — 50 BB and brisk blinds. A full game in ~10 minutes.',
    startingBB: 50,
    blindGrowth: 1.5,
    blindsIncrease: 6,
    // Capped so the final heads-up plays ~12 BB deep and resolves cleanly
    // (cap ≈ total chips / ~24 at a ~5-handed table) instead of dragging.
    maxBlind: 500,
    estMinutes: 10,
  },
  {
    id: 'tournament',
    label: 'Tournament',
    desc: ['100 BB', 'steady growth', '~20 min'],
    blurb: 'The standard tournament — 100 BB, room to actually play. ~20 minutes.',
    startingBB: 100,
    blindGrowth: 1.5,
    blindsIncrease: 10,
    maxBlind: 1000, // ~12 BB heads-up at a ~5-handed table
    estMinutes: 20,
  },
  {
    id: 'deep',
    label: 'Deep Stack',
    desc: ['200 BB', 'slow & deep', '~35 min'],
    blurb: 'Deep stack — 200 BB, lots of postflop play. The long game (~35 min).',
    startingBB: 200,
    blindGrowth: 1.5,
    blindsIncrease: 12,
    maxBlind: 2000, // ~12 BB heads-up at a ~5-handed table
    estMinutes: 35,
  },
];

/** Absolute starting stack for a preset (BB depth × base BB). */
export const presetStack = (p: BlindPreset): number => p.startingBB * PRESET_BIG_BLIND;

/** Default Custom Game structure = the standard "Tournament" preset. */
export const DEFAULT_PRESET: BlindPreset =
  BLIND_PRESETS.find((p) => p.id === 'tournament') ?? BLIND_PRESETS[0];

// ─── Custom Game structure dropdown ranges (sane defaults) ───────────────
export const STACK_OPTIONS = [1000, 2500, 5000, 10000, 20000];
export const BLIND_OPTIONS = [10, 25, 50, 100, 200];
export const BLIND_GROWTH_OPTIONS = [1.25, 1.5, 2];
// Hands between blind increases. Dropped the old "every 4" (too brutal — a deep
// stack collapses in a couple of orbits) and added slower options for deep play.
export const BLINDS_INCREASE_OPTIONS = [6, 8, 10, 12, 15];
export const MAX_BLIND_OPTIONS = [200, 500, 1000, 2000, 5000, 0];
