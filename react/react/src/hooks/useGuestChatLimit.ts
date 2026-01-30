import { useState, useEffect, useRef, useCallback } from 'react';
import { useAuth } from './useAuth';

/**
 * Tracks guest chat rate limiting: one message per player action.
 * Resets when the human turn ends (awaiting_action transitions true → false).
 */
export function useGuestChatLimit(
  isHumanTurn: boolean | undefined,
  handleSendMessage: (message: string) => Promise<void>,
) {
  const { user } = useAuth();
  const isGuest = user?.is_guest ?? true;
  const [guestChatSentThisAction, setGuestChatSentThisAction] = useState(false);

  const wasAwaitingAction = useRef(false);
  useEffect(() => {
    if (wasAwaitingAction.current && !isHumanTurn) {
      setGuestChatSentThisAction(false);
    }
    wasAwaitingAction.current = !!isHumanTurn;
  }, [isHumanTurn]);

  const wrappedSendMessage = useCallback(async (message: string) => {
    try {
      await handleSendMessage(message);
      if (isGuest) {
        setGuestChatSentThisAction(true);
      }
    } catch {
      // Don't mark as sent if the request failed
    }
  }, [handleSendMessage, isGuest]);

  const guestChatDisabled = isGuest && guestChatSentThisAction;

  return { wrappedSendMessage, guestChatDisabled, isGuest };
}
