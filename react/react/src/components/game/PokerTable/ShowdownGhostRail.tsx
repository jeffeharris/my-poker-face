/**
 * ShowdownGhostRail — folded opponents shrink to small circles in the top-left
 * of the felt during showdown so active opponents get visual prominence. The
 * folded players' arc seats stay in place (dimmed via CSS); this rail is
 * additive. Extracted from PokerTable.
 */

import type { Player } from '../../../types/player';
import { config } from '../../../config';

export function ShowdownGhostRail({
  foldedOpponents,
  displayName,
}: {
  foldedOpponents: Player[];
  displayName: (player: Player) => string;
}) {
  if (foldedOpponents.length === 0) return null;
  return (
    <div className="showdown-ghost-rail" data-testid="showdown-ghost-rail">
      {foldedOpponents.map((p) => (
        <div key={p.name} className="showdown-ghost-avatar" title={displayName(p)}>
          {p.avatar_url ? (
            <img src={`${config.API_URL}${p.avatar_url}`} alt={displayName(p)} />
          ) : (
            <span className="showdown-ghost-initial">{p.name.charAt(0).toUpperCase()}</span>
          )}
        </div>
      ))}
    </div>
  );
}
