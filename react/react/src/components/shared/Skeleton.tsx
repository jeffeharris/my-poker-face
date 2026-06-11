import type { CSSProperties } from 'react';
import './Skeleton.css';

interface SkeletonProps {
  /** CSS width (number → px). Defaults to 100%. */
  width?: string | number;
  /** CSS height (number → px). */
  height?: string | number;
  /** Round (avatar/chip) placeholder. */
  circle?: boolean;
  /** Border-radius override (ignored when `circle`). */
  radius?: string;
  className?: string;
  style?: CSSProperties;
}

/**
 * A single shimmering placeholder block. Compose several inside the real layout
 * containers (reusing their classes) so the skeleton matches the loaded shape
 * exactly. Decorative — hidden from assistive tech; gate the region with
 * `aria-busy`. Shimmer stills under prefers-reduced-motion (see Skeleton.css).
 */
export function Skeleton({ width = '100%', height, circle, radius, className, style }: SkeletonProps) {
  return (
    <span
      aria-hidden="true"
      className={`skeleton${circle ? ' skeleton--circle' : ''}${className ? ` ${className}` : ''}`}
      style={{ width, height, ...(circle ? null : radius ? { borderRadius: radius } : null), ...style }}
    />
  );
}
