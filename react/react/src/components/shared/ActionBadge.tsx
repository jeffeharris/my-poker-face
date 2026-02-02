import { memo, useEffect } from 'react';
import type { Player } from '../../types/player';

export interface ActionBadgeProps {
  player: Player;
  lastKnownActions: React.MutableRefObject<Map<string, string>>;
  onFadeComplete: () => void;
}

/**
 * Renders a colored pill badge for a player's last action (CHECK, CALL, RAISE, etc.)
 * with a fade-out animation when the action is cleared between betting rounds.
 */
export const ActionBadge = memo(function ActionBadge({ player, lastKnownActions, onFadeComplete }: ActionBadgeProps) {
  // Track last known action in a ref via useEffect (not during render) to keep renders pure
  useEffect(() => {
    if (player.is_folded || player.is_all_in) {
      lastKnownActions.current.delete(player.name);
      return;
    }
    if (player.last_action) {
      lastKnownActions.current.set(player.name, player.last_action);
    }
  }, [player.last_action, player.name, player.is_folded, player.is_all_in, lastKnownActions]);

  if (player.is_folded) {
    return <div className="action-badge action-fold">FOLD</div>;
  }
  if (player.is_all_in) {
    return <div className="action-badge action-all_in">ALL-IN</div>;
  }

  const displayAction = player.last_action || lastKnownActions.current.get(player.name);
  if (!displayAction) return null;

  const isFading = !player.last_action && !!displayAction;

  return (
    <div
      className={`action-badge action-${displayAction} ${isFading ? 'fading' : ''}`}
      onAnimationEnd={isFading ? () => {
        lastKnownActions.current.delete(player.name);
        onFadeComplete();
      } : undefined}
    >
      {displayAction.toUpperCase()}
    </div>
  );
});
