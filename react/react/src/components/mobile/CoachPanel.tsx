// TODO: CoachPanel, BottomSheet, and MobileChatSheet share a drag-to-dismiss
// sheet pattern. Consider extracting a shared Sheet component.
import { useRef, useEffect, useCallback, useState } from 'react';
import type {
  CoachStats,
  CoachMessage,
  CoachMode,
  CoachProgression,
  ProgressionState,
} from '../../types/coach';
import { CoachPanelBody } from '../game/CoachDock/CoachPanelBody';
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
  progression?: CoachProgression | null;
  progressionFull?: ProgressionState | null;
  onFetchProgression?: () => Promise<void>;
  onSkipAhead?: (level: string) => Promise<void>;
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
  progression,
  progressionFull,
  onFetchProgression,
  onSkipAhead,
}: CoachPanelProps) {
  const [isClosing, setIsClosing] = useState(false);
  const sheetRef = useRef<HTMLDivElement>(null);

  // Drag-to-dismiss
  const dragStartY = useRef(0);
  const dragCurrentY = useRef(0);
  const isDragging = useRef(false);
  const dragTimeoutRef = useRef<ReturnType<typeof setTimeout>>(undefined);
  const snapTimeoutRef = useRef<ReturnType<typeof setTimeout>>(undefined);

  useEffect(() => {
    return () => {
      clearTimeout(dragTimeoutRef.current);
      clearTimeout(snapTimeoutRef.current);
    };
  }, []);

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

  const handleClose = useCallback(() => {
    setIsClosing(true);
    setTimeout(() => {
      setIsClosing(false);
      onClose();
    }, CLOSE_ANIMATION_MS);
  }, [onClose]);

  if (!isOpen) return null;

  return (
    <div className={`coach-overlay ${isClosing ? 'coach-closing' : ''}`} onClick={handleClose}>
      <div
        ref={sheetRef}
        className={`coach-sheet ${isClosing ? 'coach-sheet-closing' : ''}`}
        onClick={(e) => e.stopPropagation()}
        onTouchStart={handleDragStart}
        onTouchMove={handleDragMove}
        onTouchEnd={handleDragEnd}
      >
        <CoachPanelBody
          isOpen={isOpen}
          onClose={handleClose}
          stats={stats}
          messages={messages}
          onSendQuestion={onSendQuestion}
          isThinking={isThinking}
          mode={mode}
          onModeChange={onModeChange}
          progression={progression}
          progressionFull={progressionFull}
          onFetchProgression={onFetchProgression}
          onSkipAhead={onSkipAhead}
          showDragHandle={true}
        />
      </div>
    </div>
  );
}
