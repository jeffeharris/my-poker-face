import { memo, useEffect, useState, useMemo } from 'react';
import './InterhandTransition.css';

interface InterhandTransitionProps {
  isVisible: boolean;
  handNumber?: number;
}

/**
 * Atmospheric transition shown between hands while the deck is shuffled
 * and commentary finishes. Creates a premium "casino ritual" feel.
 */
export const InterhandTransition = memo(function InterhandTransition({
  isVisible,
  handNumber,
}: InterhandTransitionProps) {
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

  // Generate card positions for the shuffle animation
  const shuffleCards = useMemo(() => {
    return Array.from({ length: 8 }, (_, i) => ({
      id: i,
      delay: i * 0.08,
      offsetX: (i - 3.5) * 4,
      rotation: (i - 3.5) * 2,
    }));
  }, []);

  if (!isVisible) return null;

  return (
    <div className="interhand-transition" data-testid="interhand-transition">
      {/* Ambient background particles */}
      <div className="interhand-ambient">
        {Array.from({ length: 12 }).map((_, i) => (
          <div key={i} className="ambient-particle" style={{ '--particle-index': i } as React.CSSProperties} />
        ))}
      </div>

      {/* Main content area */}
      <div className={`interhand-content ${showContent ? 'visible' : ''}`}>
        {/* Animated card deck shuffle */}
        <div className="shuffle-deck">
          {shuffleCards.map((card) => (
            <div
              key={card.id}
              className="shuffle-card"
              style={{
                '--card-delay': `${card.delay}s`,
                '--card-offset-x': `${card.offsetX}px`,
                '--card-rotation': `${card.rotation}deg`,
              } as React.CSSProperties}
            >
              <div className="card-back-pattern">
                <div className="pattern-diamond" />
                <div className="pattern-diamond secondary" />
              </div>
            </div>
          ))}
        </div>

        {/* Status text with shimmer */}
        <div className="interhand-status">
          <span className="status-text">Shuffling</span>
          <div className="status-dots">
            <span className="dot" />
            <span className="dot" />
            <span className="dot" />
          </div>
        </div>

        {/* Hand number badge */}
        {handNumber && (
          <div className="hand-badge">
            <span className="badge-label">Next Hand</span>
            <span className="badge-number">#{handNumber + 1}</span>
          </div>
        )}
      </div>

      {/* Subtle vignette overlay */}
      <div className="interhand-vignette" />
    </div>
  );
});
