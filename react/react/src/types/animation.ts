/**
 * Animation-related type definitions for card dealing and transitions.
 */

/**
 * Transform properties for a single card during dealing animation.
 */
export interface CardTransform {
  /** Final rotation angle in degrees */
  rotation: number;
  /** Starting rotation angle (tilted into slide direction) */
  startRotation: number;
  /** Vertical offset in pixels */
  offsetY: number;
  /** Horizontal offset in pixels */
  offsetX: number;
}

/**
 * Combined transform properties for dealing two hole cards.
 */
export interface CardDealTransforms {
  /** Transform for the first card */
  card1: CardTransform;
  /** Transform for the second card */
  card2: CardTransform;
  /** Gap between cards in pixels */
  gap: number;
}
