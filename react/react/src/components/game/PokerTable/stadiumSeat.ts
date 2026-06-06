/**
 * Stadium seat positioning for the desktop poker table. Pure geometry:
 * given a player's seat offset from the human and the table size, return the
 * absolute CSS position on the felt's top arc. Players are anchored to fixed
 * positions (only the D/SB/BB buttons rotate); the heads-up showdown layout is
 * a special two-slot case. Extracted from PokerTable.tsx.
 */

import type { CSSProperties } from 'react';

// Calculate seat position based on offset from human player.
// Seat offset 1 (acts after human) = left side, higher offsets move right.
export function getStadiumSeatStyle(
  seatOffset: number,
  totalPlayers: number,
  headsUpShowdownSlot?: number
): CSSProperties {
  if (headsUpShowdownSlot !== undefined) {
    const left = headsUpShowdownSlot === 0 ? 25 : 75;
    return {
      position: 'absolute',
      left: `${left}%`,
      top: '24%',
      transform: 'translate(-50%, -50%) scale(1)',
    };
  }

  // Total opponents is totalPlayers - 1 (excluding human)
  const totalOpponents = totalPlayers - 1;

  // Map seat offset (1 to totalOpponents) to position index (0 to totalOpponents-1)
  // seatOffset 1 = leftmost (index 0), seatOffset N-1 = rightmost (index N-2)
  const positionIndex = seatOffset - 1;

  // Dynamic arc spread - tighter when fewer opponents to keep them closer together
  // Full arc (120°) for 5+ opponents, narrower for fewer
  const maxArcSpread = 120;
  const minArcSpread = 60; // For 2 opponents
  const arcSpread = totalOpponents <= 2 ? minArcSpread : totalOpponents <= 4 ? 80 : maxArcSpread;

  // Center the arc around 90° (top center)
  const centerAngle = 90;
  const startAngle = centerAngle + arcSpread / 2; // left side
  const endAngle = centerAngle - arcSpread / 2; // right side
  const angleRange = startAngle - endAngle;
  const angleStep = totalOpponents > 1 ? angleRange / (totalOpponents - 1) : 0;
  const angle = (startAngle - positionIndex * angleStep) * (Math.PI / 180);

  // Wider ellipse for stadium view - reduced radiusY to bring avatars down
  const radiusX = 42; // Horizontal radius as percentage
  const radiusY = 28; // Vertical radius as percentage (reduced to bring avatars down)

  // Calculate position on ellipse, with offset to clear the header
  const left = 50 + radiusX * Math.cos(angle);
  const top = 52 - radiusY * Math.sin(angle); // Start from 52% to position avatars

  // Dynamic scaling - larger cards when fewer opponents
  const scale = totalOpponents <= 2 ? 1.6 : totalOpponents <= 4 ? 1.3 : 1.0;

  return {
    position: 'absolute',
    left: `${left}%`,
    top: `${top}%`,
    transform: `translate(-50%, -50%) scale(${scale})`,
  };
}
