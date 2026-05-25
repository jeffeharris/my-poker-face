/**
 * ActionLog - Scrolling text log of actions
 *
 * Displays actions up to the current index with phase dividers.
 * Auto-scrolls to the latest action.
 */

import { memo, useRef, useEffect } from 'react';
import type { HandAction } from './types';

interface ActionLogProps {
  actions: HandAction[];
  currentIndex: number;
}

const ACTION_COLOR_CLASS: Record<string, string> = {
  fold: 'action-log__action--fold',
  check: 'action-log__action--check',
  call: 'action-log__action--call',
  raise: 'action-log__action--raise',
  bet: 'action-log__action--raise',
  all_in: 'action-log__action--all-in',
  post_blind: 'action-log__action--call',
};

export const ActionLog = memo(function ActionLog({ actions, currentIndex }: ActionLogProps) {
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [currentIndex]);

  const visibleActions = actions.slice(0, currentIndex + 1);

  let lastPhase = '';

  return (
    <div className="action-log" ref={scrollRef}>
      <h4 className="action-log__title">Action Log</h4>
      {visibleActions.length === 0 && <div className="action-log__empty">No actions yet</div>}
      {visibleActions.map((action, i) => {
        const showPhaseHeader = action.phase !== lastPhase;
        lastPhase = action.phase;
        const isCurrent = i === currentIndex;
        const colorClass = ACTION_COLOR_CLASS[action.action] ?? '';

        return (
          <div key={action.index}>
            {showPhaseHeader && (
              <div className="action-log__phase-header">{action.phase.replace('_', ' ')}</div>
            )}
            <div
              className={`action-log__entry ${colorClass} ${isCurrent ? 'action-log__entry--current' : ''}`}
            >
              <span className="action-log__player">{action.player_name}</span>
              <span className="action-log__action-text">
                {action.action.toUpperCase()}
                {action.amount > 0 && ` $${action.amount.toLocaleString()}`}
              </span>
            </div>
          </div>
        );
      })}
    </div>
  );
});
