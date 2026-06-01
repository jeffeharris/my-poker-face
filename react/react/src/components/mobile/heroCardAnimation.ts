import { RUNOUT_TIMING } from '../../constants/runoutTiming';

/**
 * CSS `animation` shorthand for one hero hole card during the all-in run-out.
 * Priority: retreating (pull back down as the board deals) → committed (present
 * over the board, held) → dealing (the normal deal-in) → none. `side` only swaps
 * the keyframe direction (Left/Right); the right card trails the left by a stagger
 * on the present + deal-in beats (but not the retreat — both pull down together).
 * Durations/stagger/easing come from RUNOUT_TIMING.hero; the keyframe *shape*
 * (reach/spread/tilt + the equity-driven commit-* variant vars) lives in
 * MobilePokerTable.css.
 *
 * Shared by MobilePokerTable (the real table), the dev RunoutCommitSandbox, and
 * the desktop PlayerCommandCenter. The optional `keyframePrefix` lets a consumer
 * point at its own keyframe set (desktop uses px offsets, mobile uses dvh) so the
 * two stylesheets — both global in the SPA — don't collide on shared names.
 */
export function heroCardAnimation(
  side: 'Left' | 'Right',
  flags: { heroRetreating: boolean; heroCommitted: boolean; isDealing: boolean },
  keyframePrefix = ''
): string {
  const { presentSec, retreatSec, card2StaggerSec, easing } = RUNOUT_TIMING.hero;
  const stagger = side === 'Right' ? ` ${card2StaggerSec}s` : '';
  const pullDown = `${keyframePrefix}heroPullDown${side}`;
  const presentUp = `${keyframePrefix}heroPresentUp${side}`;
  const dealIn = `${keyframePrefix}dealCardIn`;
  if (flags.heroRetreating) return `${pullDown} ${retreatSec}s ${easing} forwards`;
  if (flags.heroCommitted) return `${presentUp} ${presentSec}s ${easing}${stagger} forwards`;
  if (flags.isDealing) return `${dealIn} ${presentSec}s ${easing}${stagger} both`;
  return 'none';
}
