import { memo, useEffect, useRef, useState, useCallback } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { GraduationCap, X, MessageCircleQuestion, Send } from 'lucide-react';
import type { CoachStats, FeedbackPromptData } from '../../types/coach';
import type { CoachingModeValue } from '../../types/coach';
import './CoachBubble.css';

const FEEDBACK_OPTIONS = [
  { label: 'Had a read', value: 'read' },
  { label: 'Not sure', value: 'unsure' },
];

interface CoachBubbleProps {
  isVisible: boolean;
  tip: string | null;
  stats: CoachStats | null;
  onTap: () => void;
  onDismiss: () => void;
  coachingMode?: CoachingModeValue;
  // Feedback prompt props
  feedbackPrompt?: FeedbackPromptData | null;
  onFeedbackSubmit?: (reason: string) => void;
  onFeedbackDismiss?: () => void;
}

const AUTO_DISMISS_MS = 8000;

export const CoachBubble = memo(function CoachBubble({
  isVisible,
  tip,
  stats,
  onTap,
  onDismiss,
  coachingMode,
  feedbackPrompt,
  onFeedbackSubmit,
  onFeedbackDismiss,
}: CoachBubbleProps) {
  const timerRef = useRef<ReturnType<typeof setTimeout>>(undefined);
  const [isExpanded, setIsExpanded] = useState(false);
  const [customReason, setCustomReason] = useState('');

  const isFeedbackMode = !!feedbackPrompt;
  const effectiveDismiss = isFeedbackMode ? onFeedbackDismiss || onDismiss : onDismiss;

  // Reset state when tip changes or bubble hides
  useEffect(() => {
    if (!isVisible) {
      setIsExpanded(false);
      setCustomReason('');
    }
  }, [isVisible, tip]);

  useEffect(() => {
    // Don't auto-dismiss feedback prompts - user needs to respond
    if (isFeedbackMode) {
      clearTimeout(timerRef.current);
      return;
    }
    if (isVisible && tip && !isExpanded) {
      timerRef.current = setTimeout(onDismiss, AUTO_DISMISS_MS);
      return () => clearTimeout(timerRef.current);
    }
    // Don't auto-dismiss when expanded
    if (isExpanded) {
      clearTimeout(timerRef.current);
    }
  }, [isVisible, tip, onDismiss, isExpanded, isFeedbackMode]);

  const handleExpand = useCallback((e: React.MouseEvent) => {
    e.stopPropagation();
    setIsExpanded(prev => !prev);
  }, []);

  const handleFeedbackOption = useCallback((value: string) => {
    onFeedbackSubmit?.(value);
  }, [onFeedbackSubmit]);

  const handleCustomSubmit = useCallback(() => {
    if (customReason.trim()) {
      onFeedbackSubmit?.(customReason.trim());
    }
  }, [customReason, onFeedbackSubmit]);

  const keyStat = stats?.equity != null
    ? `${Math.round(stats.equity * 100)}% equity`
    : stats?.hand_strength
      ? stats.hand_strength
      : null;

  const modeClass = coachingMode === 'learn'
    ? 'coach-bubble--learn'
    : coachingMode === 'compete'
      ? 'coach-bubble--compete'
      : '';

  const showBubble = isVisible && (tip || feedbackPrompt);

  return (
    <AnimatePresence>
      {showBubble && (
        <motion.div
          className={`coach-bubble ${modeClass} ${isExpanded ? 'coach-bubble--expanded' : ''} ${isFeedbackMode ? 'coach-bubble--feedback' : ''}`}
          initial={{ opacity: 0, y: 30, scale: 0.95 }}
          animate={{ opacity: 1, y: 0, scale: 1 }}
          exit={{ opacity: 0, y: -20, scale: 0.95 }}
          transition={{ type: 'spring', stiffness: 400, damping: 30 }}
          onClick={isFeedbackMode ? undefined : onTap}
        >
          <button
            className="coach-bubble-dismiss"
            onClick={(e) => {
              e.stopPropagation();
              effectiveDismiss();
            }}
            aria-label={isFeedbackMode ? "Dismiss feedback" : "Dismiss coach tip"}
          >
            <X size={16} />
          </button>

          {isFeedbackMode ? (
            // Feedback prompt mode
            <div className="coach-bubble-feedback">
              <div className="coach-bubble-main">
                <div className="coach-bubble-icon">
                  <MessageCircleQuestion size={18} />
                </div>
                <div className="coach-bubble-content">
                  <span className="coach-bubble__mode-label">Quick Question</span>
                  <span className="coach-bubble-tip">
                    You folded <strong>{feedbackPrompt.hand || 'that hand'}</strong> from {feedbackPrompt.position}.
                    That's in your range (top {Math.round(feedbackPrompt.range_target * 100)}%). Why?
                  </span>
                </div>
              </div>
              <div className="coach-bubble-options">
                {FEEDBACK_OPTIONS.map((option) => (
                  <button
                    key={option.value}
                    className="coach-bubble-option"
                    onClick={() => handleFeedbackOption(option.value)}
                  >
                    {option.label}
                  </button>
                ))}
              </div>
              <form
                className="coach-bubble-custom"
                onSubmit={(e) => {
                  e.preventDefault();
                  if (customReason.trim()) handleCustomSubmit();
                }}
              >
                <input
                  type="text"
                  className="coach-bubble-input"
                  placeholder="Or tell me why..."
                  value={customReason}
                  onChange={(e) => setCustomReason(e.target.value)}
                  maxLength={100}
                />
                <button
                  type="submit"
                  className={`coach-bubble-send ${customReason.trim() ? 'coach-bubble-send--active' : ''}`}
                  disabled={!customReason.trim()}
                  aria-label="Send feedback"
                >
                  <Send size={18} />
                </button>
              </form>
            </div>
          ) : (
            // Normal tip mode
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
                <span className={`coach-bubble-tip ${isExpanded ? 'coach-bubble-tip--expanded' : ''}`}>{tip}</span>
                {keyStat && (
                  <span className="coach-bubble-stat">{keyStat}</span>
                )}
              </div>
              <button
                className="coach-bubble-expand"
                onClick={handleExpand}
                aria-label={isExpanded ? 'Collapse tip' : 'Expand tip'}
              >
                {isExpanded ? 'less' : 'more'}
              </button>
            </div>
          )}
        </motion.div>
      )}
    </AnimatePresence>
  );
});
