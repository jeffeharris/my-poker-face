import { memo, useEffect, useRef } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { GraduationCap, X } from 'lucide-react';
import type { CoachStats } from '../../types/coach';
import type { CoachingModeValue } from '../../types/coach';
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

export const CoachBubble = memo(function CoachBubble({ isVisible, tip, stats, onTap, onDismiss, coachingMode }: CoachBubbleProps) {
  const timerRef = useRef<ReturnType<typeof setTimeout>>(undefined);

  useEffect(() => {
    if (isVisible && tip) {
      timerRef.current = setTimeout(onDismiss, AUTO_DISMISS_MS);
      return () => clearTimeout(timerRef.current);
    }
  }, [isVisible, tip, onDismiss]);

  const keyStat = stats?.equity != null
    ? `${Math.round(stats.equity * 100)}% equity`
    : stats?.hand_strength
      ? stats.hand_strength
      : null;

  return (
    <AnimatePresence>
      {isVisible && tip && (
        <motion.div
          className={`coach-bubble ${coachingMode === 'learn' ? 'coach-bubble--learn' : coachingMode === 'compete' ? 'coach-bubble--compete' : ''}`}
          initial={{ opacity: 0, y: 30, scale: 0.95 }}
          animate={{ opacity: 1, y: 0, scale: 1 }}
          exit={{ opacity: 0, y: -20, scale: 0.95 }}
          transition={{ type: 'spring', stiffness: 400, damping: 30 }}
          onClick={onTap}
        >
          <div className="coach-bubble-icon">
            <GraduationCap size={18} />
          </div>
          <div className="coach-bubble-content">
            {coachingMode && (
              <span className="coach-bubble__mode-label">
                {coachingMode === 'learn' ? 'Coach Tip' : 'Your Stats'}
              </span>
            )}
            <span className="coach-bubble-tip">{tip}</span>
            {keyStat && (
              <span className="coach-bubble-stat">{keyStat}</span>
            )}
          </div>
          <button
            className="coach-bubble-dismiss"
            onClick={(e) => {
              e.stopPropagation();
              onDismiss();
            }}
            aria-label="Dismiss coach tip"
          >
            <X size={18} />
          </button>
        </motion.div>
      )}
    </AnimatePresence>
  );
});
