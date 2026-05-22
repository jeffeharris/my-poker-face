import { memo, useEffect, useState } from 'react';
import './ShuffleLoading.css';

/** Static card positions for the shuffle animation. */
const SHUFFLE_CARDS = Array.from({ length: 8 }, (_, i) => ({
  id: i,
  delay: i * 0.08,
  offsetX: (i - 3.5) * 4,
  rotation: (i - 3.5) * 2,
}));

const EXIT_MS = { fade: 400, slide: 500 };

export interface FoldoutFlavor {
  headline: string;
  /** Key-value stat rows rendered as a small scoreboard. */
  rows?: { label: string; value: string }[];
}

export interface ShuffleQuote {
  text: string;
  attribution: string;
}

interface ShuffleLoadingProps {
  isVisible: boolean;
  message: string;
  submessage?: string;
  handNumber?: number;
  /** When provided, replaces the "Next Hand #N" badge with a flavor block
   *  (headline + stat chips + quip). Used for inter-hand fold-out displays. */
  foldoutFlavor?: FoldoutFlavor;
  /** Optional quote rendered above the shuffling deck. Parent owns selection
   *  so the quote stays stable across re-renders. */
  quote?: ShuffleQuote;
  /** 'overlay' (default): single full-screen layer at z-overlay.
   *  'interhand': two-layer split so avatars stay visible between layers. */
  variant?: 'overlay' | 'interhand';
  /** 'fade' (default): opacity fade-out. 'slide': slide off screen to the left. */
  exitStyle?: 'fade' | 'slide';
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
  foldoutFlavor,
  variant = 'overlay',
  exitStyle = 'fade',
  quote,
}: ShuffleLoadingProps) {
  const [showContent, setShowContent] = useState(false);
  // Keep component mounted during fade-out
  const [mounted, setMounted] = useState(false);
  const [fadingOut, setFadingOut] = useState(false);

  // Mount immediately when visible; animate out then unmount when hidden
  useEffect(() => {
    if (isVisible) {
      setMounted(true);
      setFadingOut(false);
    } else if (mounted) {
      setFadingOut(true);
      const timer = setTimeout(() => {
        setMounted(false);
        setFadingOut(false);
      }, EXIT_MS[exitStyle]);
      return () => clearTimeout(timer);
    }
  }, [isVisible, mounted, exitStyle]);

  // Stagger content appearance for dramatic effect
  useEffect(() => {
    if (isVisible) {
      const timer = setTimeout(() => setShowContent(true), 100);
      return () => clearTimeout(timer);
    } else {
      setShowContent(false);
    }
  }, [isVisible]);

  if (!mounted) return null;

  const enterClass = exitStyle === 'slide' ? ' shuffle-loading-slide-in' : '';
  const exitClass = fadingOut
    ? (exitStyle === 'slide' ? ' shuffle-loading-slide-out' : ' shuffle-loading-fade-out')
    : '';

  const content = (
    <div className={`shuffle-loading-content ${showContent ? 'visible' : ''}`}>
      {quote && (
        <div className="shuffle-loading-quote">
          <p className="shuffle-loading-quote-text">{quote.text}</p>
          <p className="shuffle-loading-quote-attribution">{quote.attribution}</p>
        </div>
      )}

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

      <div className="shuffle-loading-status">
        <span className="shuffle-loading-text">{message}</span>
        <div className="shuffle-loading-dots">
          <span className="dot" />
          <span className="dot" />
          <span className="dot" />
        </div>
      </div>

      {submessage && (
        <p className="shuffle-loading-submessage">{submessage}</p>
      )}

      {/* Fold-out flavor (headline + stat chips + quip) — replaces the
       *   "Next Hand #N" badge when present. */}
      {foldoutFlavor ? (
        <div className="shuffle-loading-flavor">
          <div className="shuffle-loading-flavor-headline">{foldoutFlavor.headline}</div>
          {foldoutFlavor.rows && foldoutFlavor.rows.length > 0 && (
            <div className="shuffle-loading-flavor-rows">
              {foldoutFlavor.rows.map((row, i) => (
                <div key={i} className="shuffle-loading-flavor-row">
                  <span className="shuffle-loading-flavor-row-label">{row.label}</span>
                  <span className="shuffle-loading-flavor-row-value">{row.value}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      ) : (
        handNumber != null && handNumber > 0 && (
          <div className="shuffle-loading-badge">
            <span className="shuffle-loading-badge-label">Next Hand</span>
            <span className="shuffle-loading-badge-number">#{handNumber + 1}</span>
          </div>
        )
      )}
    </div>
  );

  if (variant === 'interhand') {
    return (
      <>
        {/* LAYER 1: Dim background - BELOW avatars */}
        <div className={`shuffle-loading-dim${exitClass}`} data-testid="shuffle-loading-interhand">
          <div className="shuffle-loading-vignette" />
        </div>

        {/* LAYER 2: Content - ABOVE avatars */}
        <div className={`shuffle-loading-content-layer${exitClass}`}>
          {content}
        </div>
      </>
    );
  }

  // overlay variant: single full-screen layer
  return (
    <div className={`shuffle-loading-overlay${enterClass}${exitClass}`} data-testid="shuffle-loading">
      {content}
    </div>
  );
});
