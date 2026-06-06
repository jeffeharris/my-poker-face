/**
 * useMobileCoach — the coach integration for the mobile table. Wraps `useCoach`
 * and adds the table-level glue that lived inline in MobilePokerTable:
 *   • coachEnabled (guest-gated + mode-aware) and the recommended-action values
 *     fed to MobileActionButtons
 *   • the mode toggle (persists the prior mode across off/on via localStorage)
 *   • post-hand review fetch on hand end
 *   • clearing the unread-review indicator when the panel opens
 *   • staggered skill-unlock toasts
 *
 * Returns the raw `coach` object (the coach UI components consume it directly)
 * plus the derived table-facing values.
 */

import { useCallback, useEffect } from 'react';
import toast from 'react-hot-toast';
import { useCoach } from './useCoach';
import { logger } from '../utils/logger';
import type { WinnerInfo } from '../types/game';

export interface MobileCoach {
  coach: ReturnType<typeof useCoach>;
  coachEnabled: boolean;
  recommendedAction: string | null;
  raiseToAmount: number | null;
  handleCoachToggle: () => void;
}

export function useMobileCoach({
  gameId,
  playerName,
  isPlayerTurn,
  isGuest,
  winnerInfo,
  showCoachPanel,
}: {
  gameId: string | null;
  playerName: string;
  isPlayerTurn: boolean;
  isGuest: boolean;
  winnerInfo: WinnerInfo | null;
  showCoachPanel: boolean;
}): MobileCoach {
  const coach = useCoach({ gameId, playerName, isPlayerTurn });

  const coachEnabled = !isGuest && coach.mode !== 'off';

  // Coach recommendation values fed to MobileActionButtons.
  // - Proactive mode: show coach's recommendation after a proactive tip
  // - Reactive mode: only after the player asks a question
  // - Off mode: no highlighting
  const recommendedAction = coach.mode === 'off' ? null : coach.coachAction;
  const raiseToAmount = coach.mode === 'off' ? null : coach.coachRaiseTo;

  const handleCoachToggle = useCallback(() => {
    try {
      if (coachEnabled) {
        // Save current mode before turning off
        localStorage.setItem('coach_mode_before_off', coach.mode);
        coach.setMode('off');
      } else {
        // Restore previous mode
        const previous = localStorage.getItem('coach_mode_before_off');
        coach.setMode(previous === 'proactive' || previous === 'reactive' ? previous : 'reactive');
      }
    } catch (err) {
      logger.warn('localStorage unavailable for coach mode toggle:', err);
      coach.setMode(coachEnabled ? 'off' : 'reactive');
    }
  }, [coachEnabled, coach]);

  // When a hand ends, request a post-hand review from the coach.
  // coach.mode is omitted: we only want to trigger on winnerInfo change,
  // not re-fire when mode toggles while a winner banner is showing.
  useEffect(() => {
    if (winnerInfo && coach.mode !== 'off') {
      coach.fetchHandReview();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [winnerInfo, coach.fetchHandReview]);

  // Clear unread review indicator when coach panel is opened.
  // coach.hasUnreadReview is omitted: we only want to clear when the panel
  // opens, not re-fire when a new review arrives while the panel is already open.
  useEffect(() => {
    if (showCoachPanel && coach.hasUnreadReview) {
      coach.clearUnreadReview();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [showCoachPanel, coach.clearUnreadReview]);

  // Skill unlock toasts — show all staggered, then dismiss entire batch
  useEffect(() => {
    if (coach.skillUnlockQueue.length === 0) return;

    // Snapshot the queue and dismiss immediately so the effect won't re-fire
    const batch = [...coach.skillUnlockQueue];
    batch.forEach((id) => coach.dismissSkillUnlock(id));

    const timers = batch.map((skillId, i) => {
      const skillName =
        coach.progression?.skill_states[skillId]?.name ?? skillId.replace(/_/g, ' ');
      return setTimeout(() => {
        toast(`New skill unlocked: ${skillName}`, {
          duration: 4000,
          style: {
            background: 'rgba(20, 22, 30, 0.95)',
            color: '#eee',
            border: '1px solid rgba(52, 211, 153, 0.3)',
            borderRadius: '12px',
            fontSize: '13px',
          },
        });
      }, i * 600);
    });

    return () => {
      timers.forEach(clearTimeout);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [coach.skillUnlockQueue]);

  return { coach, coachEnabled, recommendedAction, raiseToAmount, handleCoachToggle };
}
