// Shared types + helpers for preflop swipe drills. Split out of PreflopCard.tsx
// so that file only exports components (keeps react-refresh / fast-refresh happy).

export interface Spot {
  scenario: string;
  position: string;
  hand: string;
  depth_bb: number;
  num_players: number;
  archetype?: string | null; // set by "what would <archetype> do" read drills
}

export interface Grade {
  verdict: 'good' | 'thin' | 'leak';
  action: string;
  your_freq: number;
  chart_freq: { fold: number; call: number; raise: number };
  primary_action: string;
}

export const pct = (x: number) => Math.round(x * 100);

// Long-form position names for the situation header.
export const POSITION_NAME: Record<string, string> = {
  UTG: 'Under the gun',
  HJ: 'Hijack',
  CO: 'Cutoff',
  BTN: 'Button',
  SB: 'Small blind',
  BB: 'Big blind',
};

// RFI-openable seats (BB never opens — no rfi chart). Drills facing a raise use
// their own seat list.
export const RFI_POS = ['UTG', 'HJ', 'CO', 'BTN', 'SB'];

const sameSpot = (a: Spot, b: Spot) => a.position === b.position && a.hand === b.hand;

// Draw a random spot from the pool, avoiding an immediate repeat.
export function drawNext(pool: Spot[], avoid?: Spot | null): Spot | null {
  if (pool.length === 0) return null;
  if (pool.length === 1) return pool[0];
  let pick = pool[Math.floor(Math.random() * pool.length)];
  while (avoid && sameSpot(pick, avoid)) pick = pool[Math.floor(Math.random() * pool.length)];
  return pick;
}
