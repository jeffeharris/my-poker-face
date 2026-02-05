import { memo, useEffect, useRef, useState, useCallback } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { MessageCircleQuestion, X } from 'lucide-react';
import './CoachFeedback.css';

interface FeedbackOption {
  label: string;
  value: string;
}

interface CoachFeedbackProps {
  isVisible: boolean;
  hand: string;
  position: string;
  rangeTarget: number;
  options?: FeedbackOption[];
  onDismiss: () => void;
  onSubmit: (reason: string) => void;
}

const AUTO_DISMISS_MS = 8000;

// Default options if coach doesn't provide custom ones
const DEFAULT_OPTIONS: FeedbackOption[] = [
  { label: 'Had a read', value: 'read' },
  { label: 'Too many players', value: 'multiway' },
  { label: 'Playing cautious', value: 'cautious' },
  { label: 'Not sure', value: 'unsure' },
];

export const CoachFeedback = memo(function CoachFeedback({
  isVisible,
  hand,
  position,
  rangeTarget,
  options = DEFAULT_OPTIONS,
  onDismiss,
  onSubmit,
}: CoachFeedbackProps) {
  const timerRef = useRef<ReturnType<typeof setTimeout>>();
  const [showCustomInput, setShowCustomInput] = useState(false);
  const [customText, setCustomText] = useState('');

  // Reset state when visibility changes
  useEffect(() => {
    if (!isVisible) {
      setShowCustomInput(false);
      setCustomText('');
    }
  }, [isVisible]);

  // Auto-dismiss timer
  useEffect(() => {
    if (isVisible && !showCustomInput) {
      timerRef.current = setTimeout(onDismiss, AUTO_DISMISS_MS);
      return () => clearTimeout(timerRef.current);
    }
    // Pause auto-dismiss when custom input is shown
    if (showCustomInput) {
      clearTimeout(timerRef.current);
    }
  }, [isVisible, showCustomInput, onDismiss]);

  const handleOptionClick = useCallback((value: string) => {
    onSubmit(value);
    onDismiss();
  }, [onSubmit, onDismiss]);

  const handleCustomSubmit = useCallback(() => {
    if (customText.trim()) {
      onSubmit(customText.trim());
      onDismiss();
    }
  }, [customText, onSubmit, onDismiss]);

  const rangePercent = Math.round(rangeTarget * 100);

  return (
    <AnimatePresence>
      {isVisible && (
        <motion.div
          className="coach-feedback"
          initial={{ opacity: 0, y: 30, scale: 0.95 }}
          animate={{ opacity: 1, y: 0, scale: 1 }}
          exit={{ opacity: 0, y: -20, scale: 0.95 }}
          transition={{ type: 'spring', stiffness: 400, damping: 30 }}
        >
          <button
            className="coach-feedback-dismiss"
            onClick={onDismiss}
            aria-label="Dismiss feedback"
          >
            <X size={16} />
          </button>

          <div className="coach-feedback-header">
            <div className="coach-feedback-icon">
              <MessageCircleQuestion size={20} />
            </div>
            <div className="coach-feedback-title">
              <span className="coach-feedback-label">Coach Note</span>
              <span className="coach-feedback-hand">
                You folded <strong>{hand}</strong> from {position}
              </span>
            </div>
          </div>

          <p className="coach-feedback-message">
            That hand is in your range (top {rangePercent}%) for this position.
            Why did you fold?
          </p>

          {!showCustomInput ? (
            <div className="coach-feedback-options">
              {options.map((option) => (
                <button
                  key={option.value}
                  className="coach-feedback-option"
                  onClick={() => handleOptionClick(option.value)}
                >
                  {option.label}
                </button>
              ))}
              <button
                className="coach-feedback-option coach-feedback-option--other"
                onClick={() => setShowCustomInput(true)}
              >
                Other...
              </button>
            </div>
          ) : (
            <div className="coach-feedback-custom">
              <input
                type="text"
                className="coach-feedback-input"
                placeholder="Tell me why..."
                value={customText}
                onChange={(e) => setCustomText(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && customText.trim()) {
                    handleCustomSubmit();
                  }
                }}
                autoFocus
                maxLength={100}
              />
              <div className="coach-feedback-custom-actions">
                <button
                  className="coach-feedback-cancel"
                  onClick={() => setShowCustomInput(false)}
                >
                  Back
                </button>
                <button
                  className="coach-feedback-submit"
                  onClick={handleCustomSubmit}
                  disabled={!customText.trim()}
                >
                  Submit
                </button>
              </div>
            </div>
          )}
        </motion.div>
      )}
    </AnimatePresence>
  );
});
