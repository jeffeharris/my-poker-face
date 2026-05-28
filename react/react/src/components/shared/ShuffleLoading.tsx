import { memo, useEffect, useState, type ReactNode } from 'react';
import './ShuffleLoading.css';

/** Static card positions for the shuffle animation. */
const SHUFFLE_CARDS = Array.from({ length: 8 }, (_, i) => ({
  id: i,
  delay: i * 0.08,
  offsetX: (i - 3.5) * 4,
  rotation: (i - 3.5) * 2,
}));

const EXIT_MS = { fade: 400, slide: 500 };

export interface ShuffleQuote {
  text: string;
  attribution: string;
}

/** One row of the interhand world ticker (cash/career mode). The parent
 *  owns icon + message so this component stays free of feature-specific
 *  event knowledge. */
export interface TickerLine {
  key: string;
  icon?: ReactNode;
  message: string;
}

interface ShuffleLoadingProps {
  isVisible: boolean;
  message: string;
  submessage?: string;
  handNumber?: number;
  /** When non-empty, a "meanwhile, elsewhere" world-ticker strip renders in
   *  place of the hand-number badge (cash/career mode). */
  ticker?: TickerLine[];
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
  ticker,
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
    ? exitStyle === 'slide'
      ? ' shuffle-loading-slide-out'
      : ' shuffle-loading-fade-out'
    : '';

  // Three always-rendered bands so each component owns reserved space and
  // content changes (quote length, ticker rows streaming in) can't shift the
  // others. The 40/20/40 fixed-height grid only kicks in for the `interhand`
  // variant; the `overlay` variant keeps its content-sized centered column
  // because the band wrappers fall back to `display: contents` there.
  const content = (
    <div
      className={`shuffle-loading-content ${showContent ? 'visible' : ''}${
        variant === 'interhand' ? ' shuffle-loading-content--bands' : ''
      }`}
    >
      {/* Band 1 — quote (top 40%, centered) */}
      <div className="shuffle-loading-band shuffle-loading-band--quote">
        {quote && (
          <div className="shuffle-loading-quote">
            <p className="shuffle-loading-quote-text">{quote.text}</p>
            <p className="shuffle-loading-quote-attribution">{quote.attribution}</p>
          </div>
        )}
      </div>

      {/* Band 2 — shuffle deck + status (middle 20%) */}
      <div className="shuffle-loading-band shuffle-loading-band--deck">
        <div className="shuffle-loading-deck">
          {SHUFFLE_CARDS.map((card) => (
            <div
              key={card.id}
              className="shuffle-loading-card"
              style={
                {
                  '--card-delay': `${card.delay}s`,
                  '--card-offset-x': `${card.offsetX}px`,
                  '--card-rotation': `${card.rotation}deg`,
                } as React.CSSProperties
              }
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

        {submessage && <p className="shuffle-loading-submessage">{submessage}</p>}
      </div>

      {/* Band 3 — recent activity (bottom 40%). Cash/career mode: a "meanwhile,
          elsewhere" world ticker. Tournament mode falls through to the
          hand-number badge. */}
      <div className="shuffle-loading-band shuffle-loading-band--activity">
        {ticker && ticker.length > 0 ? (
          <div className="shuffle-loading-ticker" aria-label="Meanwhile, around the room">
            <span className="shuffle-loading-ticker-label">Meanwhile…</span>
            <ul className="shuffle-loading-ticker-list">
              {ticker.map((line) => (
                <li key={line.key} className="shuffle-loading-ticker-item">
                  {line.icon}
                  <span className="shuffle-loading-ticker-message">{line.message}</span>
                </li>
              ))}
            </ul>
          </div>
        ) : (
          handNumber != null &&
          handNumber > 0 && (
            <div className="shuffle-loading-badge">
              <span className="shuffle-loading-badge-label">Next Hand</span>
              <span className="shuffle-loading-badge-number">#{handNumber + 1}</span>
            </div>
          )
        )}
      </div>
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
        <div className={`shuffle-loading-content-layer${exitClass}`}>{content}</div>
      </>
    );
  }

  // overlay variant: single full-screen layer
  return (
    <div
      className={`shuffle-loading-overlay${enterClass}${exitClass}`}
      data-testid="shuffle-loading"
    >
      {content}
    </div>
  );
});
