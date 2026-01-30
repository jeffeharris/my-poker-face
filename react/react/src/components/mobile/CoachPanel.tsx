// TODO: CoachPanel, BottomSheet, and MobileChatSheet share a drag-to-dismiss
// sheet pattern. Consider extracting a shared Sheet component.
import { useState, useRef, useEffect, useCallback } from 'react';
import { X, Send } from 'lucide-react';
import type { CoachStats, CoachMessage, CoachMode } from '../../types/coach';
import { StatsBar } from './StatsBar';
import './CoachPanel.css';

const CLOSE_ANIMATION_MS = 250;

interface CoachPanelProps {
  isOpen: boolean;
  onClose: () => void;
  stats: CoachStats | null;
  messages: CoachMessage[];
  onSendQuestion: (question: string) => Promise<void>;
  isThinking: boolean;
  mode: CoachMode;
  onModeChange: (mode: CoachMode) => void;
}

export function CoachPanel({
  isOpen,
  onClose,
  stats,
  messages,
  onSendQuestion,
  isThinking,
  mode,
  onModeChange,
}: CoachPanelProps) {
  const [inputValue, setInputValue] = useState('');
  const [isClosing, setIsClosing] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const sheetRef = useRef<HTMLDivElement>(null);
  const wasOpenRef = useRef(false);

  // Drag-to-dismiss
  const dragStartY = useRef(0);
  const dragCurrentY = useRef(0);
  const isDragging = useRef(false);
  const dragTimeoutRef = useRef<ReturnType<typeof setTimeout>>();
  const snapTimeoutRef = useRef<ReturnType<typeof setTimeout>>();

  useEffect(() => {
    return () => {
      clearTimeout(dragTimeoutRef.current);
      clearTimeout(snapTimeoutRef.current);
    };
  }, []);

  // Scroll to bottom: instant on open, smooth for new messages
  useEffect(() => {
    if (isOpen && messagesEndRef.current) {
      const justOpened = !wasOpenRef.current;
      wasOpenRef.current = true;
      setTimeout(() => {
        messagesEndRef.current?.scrollIntoView({
          behavior: justOpened ? 'instant' : 'smooth',
        });
      }, justOpened ? 0 : 100);
    }
    if (!isOpen) {
      wasOpenRef.current = false;
    }
  }, [isOpen, messages.length]);

  const handleDragStart = useCallback((e: React.TouchEvent) => {
    isDragging.current = true;
    dragStartY.current = e.touches[0].clientY;
    dragCurrentY.current = 0;
    if (sheetRef.current) {
      sheetRef.current.style.transition = 'none';
    }
  }, []);

  const handleDragMove = useCallback((e: React.TouchEvent) => {
    if (!isDragging.current) return;
    const delta = e.touches[0].clientY - dragStartY.current;
    dragCurrentY.current = Math.max(0, delta);
    if (sheetRef.current) {
      sheetRef.current.style.transform = `translateY(${dragCurrentY.current}px)`;
    }
  }, []);

  const handleDragEnd = useCallback(() => {
    if (!isDragging.current) return;
    isDragging.current = false;
    const sheet = sheetRef.current;
    if (!sheet) return;

    const threshold = sheet.offsetHeight * 0.3;
    if (dragCurrentY.current > threshold) {
      sheet.style.transition = 'transform 0.25s ease-in';
      sheet.style.transform = 'translateY(100%)';
      dragTimeoutRef.current = setTimeout(() => {
        sheet.style.transition = '';
        sheet.style.transform = '';
        onClose();
      }, 250);
    } else {
      sheet.style.transition = 'transform 0.2s ease-out';
      sheet.style.transform = 'translateY(0)';
      snapTimeoutRef.current = setTimeout(() => {
        sheet.style.transition = '';
      }, 200);
    }
  }, [onClose]);

  const handleClose = () => {
    setIsClosing(true);
    setTimeout(() => {
      setIsClosing(false);
      onClose();
    }, CLOSE_ANIMATION_MS);
  };

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
    const modes: CoachMode[] = ['proactive', 'reactive', 'off'];
    const idx = modes.indexOf(mode);
    onModeChange(modes[(idx + 1) % modes.length]);
  };

  if (!isOpen) return null;

  return (
    <div
      className={`coach-overlay ${isClosing ? 'coach-closing' : ''}`}
      onClick={handleClose}
    >
      <div
        ref={sheetRef}
        className={`coach-sheet ${isClosing ? 'coach-sheet-closing' : ''}`}
        onClick={(e) => e.stopPropagation()}
      >
        <div
          className="coach-header"
          onTouchStart={handleDragStart}
          onTouchMove={handleDragMove}
          onTouchEnd={handleDragEnd}
        >
          <div className="coach-drag-handle" />
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
              <button className="coach-close-btn" onClick={handleClose} aria-label="Close coach">
                <X size={20} />
              </button>
            </div>
          </div>
        </div>

        <StatsBar stats={stats} />

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
                  {msg.type === 'review' ? 'Hand Review' : msg.type === 'tip' ? 'Tip' : msg.role === 'user' ? 'You' : 'Coach'}
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
      </div>
    </div>
  );
}
