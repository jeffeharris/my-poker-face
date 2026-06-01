import { useState, useRef, useEffect, useCallback } from 'react';
import { X, Send } from 'lucide-react';
import type {
  CoachStats,
  CoachMessage,
  CoachMode,
  CoachProgression,
  ProgressionState,
} from '../../../types/coach';
import { StatsBar } from '../../mobile/StatsBar';
import { ProgressionStrip } from '../../mobile/ProgressionStrip';
import { ProgressionDetail } from '../../mobile/ProgressionDetail';
import { safeGetItem, safeSetItem } from '../../../utils/storage';

export interface CoachPanelBodyProps {
  isOpen: boolean;
  onClose: () => void;
  stats: CoachStats | null;
  messages: CoachMessage[];
  onSendQuestion: (question: string) => Promise<void>;
  isThinking: boolean;
  mode: CoachMode;
  onModeChange: (mode: CoachMode) => void;
  progression?: CoachProgression | null;
  progressionFull?: ProgressionState | null;
  onFetchProgression?: () => Promise<void>;
  onSkipAhead?: (level: string) => Promise<void>;
  /** When true, renders a drag handle at the top (mobile sheet use). */
  showDragHandle?: boolean;
}

export function CoachPanelBody({
  isOpen,
  onClose,
  stats,
  messages,
  onSendQuestion,
  isThinking,
  mode,
  onModeChange,
  progression,
  progressionFull,
  onFetchProgression,
  onSkipAhead,
  showDragHandle = false,
}: CoachPanelBodyProps) {
  const [inputValue, setInputValue] = useState('');
  const [showDetail, setShowDetail] = useState(false);
  const [onboardingDismissed, setOnboardingDismissed] = useState(
    () => safeGetItem('coach_onboarding_dismissed') === 'true'
  );
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const wasOpenRef = useRef(false);
  const hasFetchedProgressionRef = useRef(false);

  const handleToggleDetail = useCallback(() => setShowDetail((d) => !d), []);

  // Fetch full progression on first panel open
  useEffect(() => {
    if (isOpen && !hasFetchedProgressionRef.current && onFetchProgression) {
      hasFetchedProgressionRef.current = true;
      onFetchProgression();
    }
  }, [isOpen, onFetchProgression]);

  // Scroll to bottom: instant on open, smooth for new messages
  useEffect(() => {
    if (isOpen && messagesEndRef.current) {
      const justOpened = !wasOpenRef.current;
      wasOpenRef.current = true;
      setTimeout(
        () => {
          messagesEndRef.current?.scrollIntoView({
            behavior: justOpened ? 'instant' : 'smooth',
          });
        },
        justOpened ? 0 : 100
      );
    }
    if (!isOpen) {
      wasOpenRef.current = false;
    }
  }, [isOpen, messages.length]);

  const handleSend = async () => {
    const trimmed = inputValue.trim();
    if (!trimmed || isThinking) return;
    setInputValue('');
    await onSendQuestion(trimmed);
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const cycleMode = () => {
    // Only toggle between active modes — "off" is controlled via
    // the UserDropdown toggle, not the in-panel button.
    onModeChange(mode === 'proactive' ? 'reactive' : 'proactive');
  };

  return (
    <>
      <div className="coach-header">
        {showDragHandle && <div className="coach-drag-handle" />}
        <div className="coach-header-row">
          <h3 className="coach-title">Poker Coach</h3>
          <div className="coach-header-actions">
            <button
              className={`coach-mode-btn mode-${mode}`}
              onClick={cycleMode}
              aria-label={`Coach mode: ${mode}`}
            >
              {mode === 'proactive' ? 'Auto' : mode === 'reactive' ? 'Ask' : 'Off'}
            </button>
            <button className="coach-close-btn" onClick={onClose} aria-label="Close coach">
              <X size={20} />
            </button>
          </div>
        </div>
      </div>

      <StatsBar stats={stats} />

      {progression && (
        <ProgressionStrip
          progression={progression}
          isExpanded={showDetail}
          onToggle={handleToggleDetail}
        />
      )}

      {/* Onboarding prompt for beginners who haven't completed onboarding. */}
      {!onboardingDismissed &&
        progressionFull &&
        progressionFull.profile?.self_reported_level === 'beginner' &&
        !progressionFull.profile?.onboarding_completed_at && (
          <div className="coach-onboarding">
            <p className="coach-onboarding__text">How much poker do you know?</p>
            <div className="coach-onboarding__actions">
              <button
                className="coach-onboarding__btn coach-onboarding__btn--dismiss"
                onClick={() => {
                  onSkipAhead?.('beginner');
                  setOnboardingDismissed(true);
                  safeSetItem('coach_onboarding_dismissed', 'true');
                }}
              >
                New to poker
              </button>
              <button
                className="coach-onboarding__btn coach-onboarding__btn--skip"
                onClick={() => {
                  onSkipAhead?.('intermediate');
                  setOnboardingDismissed(true);
                  safeSetItem('coach_onboarding_dismissed', 'true');
                }}
              >
                I know the basics
              </button>
              <button
                className="coach-onboarding__btn coach-onboarding__btn--skip"
                onClick={() => {
                  onSkipAhead?.('experienced');
                  setOnboardingDismissed(true);
                  safeSetItem('coach_onboarding_dismissed', 'true');
                }}
              >
                Experienced
              </button>
            </div>
          </div>
        )}

      {showDetail ? (
        <ProgressionDetail
          progressionFull={progressionFull ?? null}
          progressionLite={progression ?? null}
        />
      ) : (
        <div className="coach-messages">
          {messages.length === 0 ? (
            <div className="coach-empty">
              <span className="coach-empty-text">
                Ask me anything about your hand, odds, or strategy!
              </span>
            </div>
          ) : (
            messages.map((msg) => (
              <div
                key={msg.id}
                className={`coach-msg coach-msg-${msg.role} ${msg.type ? `coach-msg-${msg.type}` : ''}`}
              >
                <span className="coach-msg-sender">
                  {msg.type === 'review'
                    ? 'Hand Review'
                    : msg.type === 'tip'
                      ? 'Tip'
                      : msg.role === 'user'
                        ? 'You'
                        : 'Coach'}
                </span>
                <span className="coach-msg-text">{msg.content}</span>
              </div>
            ))
          )}
          {isThinking && (
            <div className="coach-msg coach-msg-coach">
              <span className="coach-msg-sender">Coach</span>
              <span className="coach-msg-text coach-thinking">Thinking...</span>
            </div>
          )}
          <div ref={messagesEndRef} />
        </div>
      )}

      <form
        className="coach-input-area"
        onSubmit={(e) => {
          e.preventDefault();
          handleSend();
        }}
      >
        <input
          type="text"
          className="coach-text-input"
          placeholder="Ask your coach..."
          value={inputValue}
          onChange={(e) => setInputValue(e.target.value)}
          onKeyDown={handleKeyDown}
          maxLength={300}
          disabled={isThinking}
        />
        <button
          type="submit"
          className={`coach-send-btn ${inputValue.trim() ? 'coach-send-active' : ''}`}
          disabled={!inputValue.trim() || isThinking}
          aria-label="Send question"
        >
          <Send size={22} aria-hidden="true" />
        </button>
      </form>
    </>
  );
}
