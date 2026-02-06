import { memo, useEffect, useState } from 'react';
import './ShuffleLoading.css';

/** Static card positions for the shuffle animation. */
const SHUFFLE_CARDS = Array.from({ length: 8 }, (_, i) => ({
  id: i,
  delay: i * 0.08,
  offsetX: (i - 3.5) * 4,
  rotation: (i - 3.5) * 2,
}));

interface ShuffleLoadingProps {
  isVisible: boolean;
  message: string;
  submessage?: string;
  handNumber?: number;
  /** 'overlay' (default): single full-screen layer at z-overlay.
   *  'interhand': two-layer split so avatars stay visible between layers. */
  variant?: 'overlay' | 'interhand';
}

/**
 * Premium shuffle-deck loading animation used for all loading states.
 *
 * - **overlay** variant: single full-screen overlay (game creation, table setup).
 * - **interhand** variant: two-layer split (dim at z-50, content at z-150)
 *   so player avatars remain visible between the layers.
 */
export const ShuffleLoading = memo(function ShuffleLoading({
  isVisible,
  message,
  submessage,
  handNumber,
  variant = 'overlay',
}: ShuffleLoadingProps) {
  const [showContent, setShowContent] = useState(false);

  // Stagger content appearance for dramatic effect
  useEffect(() => {
    if (isVisible) {
      const timer = setTimeout(() => setShowContent(true), 100);
      return () => clearTimeout(timer);
    } else {
      setShowContent(false);
    }
  }, [isVisible]);

  if (!isVisible) return null;

  const content = (
    <div className={`shuffle-loading-content ${showContent ? 'visible' : ''}`}>
      {/* Animated card deck shuffle */}
      <div className="shuffle-loading-deck">
        {SHUFFLE_CARDS.map((card) => (
          <div
            key={card.id}
            className="shuffle-loading-card"
            style={{
              '--card-delay': `${card.delay}s`,
              '--card-offset-x': `${card.offsetX}px`,
              '--card-rotation': `${card.rotation}deg`,
            } as React.CSSProperties}
          >
            <div className="shuffle-loading-card-back">
              <div className="shuffle-loading-diamond" />
              <div className="shuffle-loading-diamond secondary" />
            </div>
          </div>
        ))}
      </div>

      {/* Status text with shimmer */}
      <div className="shuffle-loading-status">
        <span className="shuffle-loading-text">{message}</span>
        <div className="shuffle-loading-dots">
          <span className="dot" />
          <span className="dot" />
          <span className="dot" />
        </div>
      </div>

      {/* Submessage */}
      {submessage && (
        <p className="shuffle-loading-submessage">{submessage}</p>
      )}

      {/* Hand number badge (interhand only) */}
      {handNumber != null && handNumber > 0 && (
        <div className="shuffle-loading-badge">
          <span className="shuffle-loading-badge-label">Next Hand</span>
          <span className="shuffle-loading-badge-number">#{handNumber + 1}</span>
        </div>
      )}
    </div>
  );

  if (variant === 'interhand') {
    return (
      <>
        {/* LAYER 1: Dim background - BELOW avatars */}
        <div className="shuffle-loading-dim" data-testid="shuffle-loading">
          <div className="shuffle-loading-vignette" />
        </div>

        {/* LAYER 2: Content - ABOVE avatars */}
        <div className="shuffle-loading-content-layer">
          {content}
        </div>
      </>
    );
  }

  // overlay variant: single full-screen layer
  return (
    <div className="shuffle-loading-overlay" data-testid="shuffle-loading">
      {content}
    </div>
  );
});
