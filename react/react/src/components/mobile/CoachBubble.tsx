import { memo, useEffect, useRef, useState, useCallback } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { GraduationCap, X } from 'lucide-react';
import type { CoachStats } from '../../types/coach';
import type { CoachingModeValue } from '../../types/coach';
import { CountdownRing } from './CountdownRing';
import './CoachBubble.css';

interface CoachBubbleProps {
  isVisible: boolean;
  tip: string | null;
  stats: CoachStats | null;
  onTap: () => void;
  onDismiss: () => void;
  coachingMode?: CoachingModeValue;
}

const AUTO_DISMISS_MS = 8000;

export const CoachBubble = memo(function CoachBubble({
  isVisible,
  tip,
  stats,
  onTap,
  onDismiss,
  coachingMode,
}: CoachBubbleProps) {
  const timerRef = useRef<ReturnType<typeof setTimeout>>(undefined);
  const [isExpanded, setIsExpanded] = useState(false);
  // Timestamp the auto-dismiss timer (re)started, for the countdown ring.
  // null while paused (expanded) or hidden so the ring sits full/frozen.
  const [dismissStartedAt, setDismissStartedAt] = useState<number | null>(null);

  // Reset state when tip changes or bubble hides
  useEffect(() => {
    if (!isVisible) {
      setIsExpanded(false);
    }
  }, [isVisible, tip]);

  useEffect(() => {
    if (isVisible && tip && !isExpanded) {
      // Stamp the (re)start so the ring drains over this window. Expanding
      // then collapsing restarts the full timer here, and the ring follows.
      setDismissStartedAt(Date.now());
      timerRef.current = setTimeout(onDismiss, AUTO_DISMISS_MS);
      return () => clearTimeout(timerRef.current);
    }
    // Expanded (paused) or hidden — freeze the ring full / clear it.
    setDismissStartedAt(null);
    if (isExpanded) {
      clearTimeout(timerRef.current);
    }
  }, [isVisible, tip, onDismiss, isExpanded]);

  const handleExpand = useCallback((e: React.MouseEvent) => {
    e.stopPropagation();
    setIsExpanded((prev) => !prev);
  }, []);

  const keyStat =
    stats?.equity != null
      ? `${Math.round(stats.equity * 100)}% equity`
      : stats?.hand_strength
        ? stats.hand_strength
        : null;

  const modeClass =
    coachingMode === 'learn'
      ? 'coach-bubble--learn'
      : coachingMode === 'compete'
        ? 'coach-bubble--compete'
        : '';

  return (
    <AnimatePresence>
      {isVisible && tip && (
        <motion.div
          className={`coach-bubble ${modeClass} ${isExpanded ? 'coach-bubble--expanded' : ''}`}
          initial={{ opacity: 0, y: 30, scale: 0.95 }}
          animate={{ opacity: 1, y: 0, scale: 1 }}
          exit={{ opacity: 0, y: -20, scale: 0.95 }}
          transition={{ type: 'spring', stiffness: 400, damping: 30 }}
          onClick={onTap}
        >
          {/* Auto-dismiss countdown, top-left (the X owns the top-right). Sits
              full and frozen while expanded, since expanding pauses dismissal. */}
          <CountdownRing
            timerStartedAt={dismissStartedAt}
            displayDuration={AUTO_DISMISS_MS}
            size={15}
            stroke={2}
            className="coach-bubble-timer-ring"
          />
          <button
            className="coach-bubble-dismiss"
            onClick={(e) => {
              e.stopPropagation();
              onDismiss();
            }}
            aria-label="Dismiss coach tip"
          >
            <X size={16} />
          </button>

          <div className="coach-bubble-main">
            <div className="coach-bubble-icon">
              <GraduationCap size={18} />
            </div>
            <div className="coach-bubble-content">
              {coachingMode && (
                <span className="coach-bubble__mode-label">
                  {coachingMode === 'learn' ? 'Coach Tip' : 'Your Stats'}
                </span>
              )}
              <span
                className={`coach-bubble-tip ${isExpanded ? 'coach-bubble-tip--expanded' : ''}`}
              >
                {tip}
              </span>
              {keyStat && <span className="coach-bubble-stat">{keyStat}</span>}
            </div>
            <button
              className="coach-bubble-expand"
              onClick={handleExpand}
              aria-label={isExpanded ? 'Collapse tip' : 'Expand tip'}
            >
              {isExpanded ? 'less' : 'more'}
            </button>
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  );
});
