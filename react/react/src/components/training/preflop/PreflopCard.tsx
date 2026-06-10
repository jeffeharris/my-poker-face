import { Card } from '../../cards';
import './preflop.css';

// Shared building blocks for preflop swipe drills (the card face + helpers).
// Drills (SwipeDrill = RFI, VsOpenDrill = facing a raise) provide their own load
// + grade flow and pass each spot to PreflopCardFace.

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

// A 169-hand shorthand → two concrete cards for display. Suited = same suit;
// offsuit / pair = two different suits (black + red reads clearest). 'T' → '10'.
function handToCards(
  hand: string
): [{ rank: string; suit: string }, { rank: string; suit: string }] {
  const isPair = hand.length === 2;
  const norm = (r: string) => (r === 'T' ? '10' : r);
  const r1 = norm(hand[0]);
  const r2 = norm(isPair ? hand[0] : hand[1]);
  const suited = !isPair && hand[2] === 's';
  return [
    { rank: r1, suit: 'Spades' },
    { rank: r2, suit: suited ? 'Spades' : 'Hearts' },
  ];
}

// Table seats in clockwise order from the button.
const SEAT_ORDER = ['BTN', 'SB', 'BB', 'UTG', 'HJ', 'CO'];
// Six screen slots, clockwise starting from the bottom (where YOU always sit).
const SLOTS: { x: number; y: number }[] = [
  { x: 50, y: 94 }, // 0 bottom — hero
  { x: 10, y: 70 }, // 1 bottom-left
  { x: 10, y: 30 }, // 2 top-left
  { x: 50, y: 6 }, //  3 top
  { x: 90, y: 30 }, // 4 top-right
  { x: 90, y: 70 }, // 5 bottom-right
];

// Mini table map: YOU are the filled emerald dot, fixed at the bottom; the red
// dealer button moves around you by your seat's offset from the button.
export function SeatMap({ position }: { position: string }) {
  const h = SEAT_ORDER.indexOf(position);
  const dealerSlot = h < 0 ? 0 : (6 - h) % 6; // clockwise seats from you to the button
  const here = POSITION_NAME[position] ?? position;
  return (
    <div className="oc-table" aria-label={`You are in ${here}; the dealer button is the red seat`}>
      {SLOTS.map((s, i) => (
        <span
          key={i}
          aria-hidden="true"
          className={
            'oc-seat' +
            (i === 0 ? ' oc-seat--hero' : '') +
            (i === dealerSlot ? ' oc-seat--dealer' : '')
          }
          style={{ left: `${s.x}%`, top: `${s.y}%` }}
        />
      ))}
      <span className="oc-table__code" aria-hidden="true">
        {position}
      </span>
    </div>
  );
}

// The shared preflop card face. Anatomy: situation (top) → your cards (middle).
// `tag` is the situational note ("Folded to you", "Facing a raise", …).
// `headline` (optional) shows a prominent line above the position — e.g. the
// opponent archetype in a "what would they do" read drill.
export function PreflopCardFace({
  spot,
  tag,
  headline,
}: {
  spot: Spot;
  tag: string;
  headline?: string;
}) {
  return (
    <>
      <div className="oc-situation">
        {headline && <span className="oc-archetype">{headline}</span>}
        <span className="oc-pos">{POSITION_NAME[spot.position] ?? spot.position}</span>
        <SeatMap position={spot.position} />
        <div className="oc-context">
          <span className="oc-chip">{spot.depth_bb}bb deep</span>
          <span className="oc-chip">{spot.num_players}-max</span>
          <span className="oc-chip oc-chip--tag">{tag}</span>
        </div>
      </div>
      <div className="oc-cards">
        <div className="oc-holes">
          {handToCards(spot.hand).map((c, i) => (
            <Card key={i} card={c} faceDown={false} size="xlarge" />
          ))}
        </div>
        <div className="oc-hand">{spot.hand}</div>
      </div>
    </>
  );
}
