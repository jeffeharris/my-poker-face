import { Card } from '../../cards';
import { POSITION_NAME, type Spot } from './preflopUtils';
import './preflop.css';

// Card-face components for preflop swipe drills. Shared types + helpers live in
// preflopUtils. Drills provide their own load/grade flow and pass each spot here.

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

// The shared preflop card face. Anatomy: a head row — the hand shorthand (JTo,
// 72s) on the left, the situation on the right — then the hole cards below.
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
      <div className="oc-head">
        <div className="oc-hand">{spot.hand}</div>
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
      </div>
      <div className="oc-holes">
        {handToCards(spot.hand).map((c, i) => (
          <Card key={i} card={c} faceDown={false} size="xlarge" />
        ))}
      </div>
    </>
  );
}
