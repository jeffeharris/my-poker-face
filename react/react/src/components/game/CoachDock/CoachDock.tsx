import { memo } from 'react';
import type {
  CoachStats,
  CoachMessage,
  CoachMode,
  CoachProgression,
  ProgressionState,
} from '../../../types/coach';
import { CoachPanelBody } from './CoachPanelBody';
import './CoachDock.css';
import '../../mobile/CoachPanel.css';

interface CoachDockProps {
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

export const CoachDock = memo(function CoachDock({
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
}: CoachDockProps) {
  if (!isOpen) return null;

  return (
    <div
      className="coach-dock"
      data-testid="coach-dock"
      role="complementary"
      aria-label="Poker Coach"
    >
      <CoachPanelBody
        isOpen={isOpen}
        onClose={onClose}
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
        showDragHandle={false}
      />
    </div>
  );
});
