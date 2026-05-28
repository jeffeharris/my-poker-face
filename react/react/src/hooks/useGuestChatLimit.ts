import { useState, useEffect, useRef, useCallback } from 'react';
import { useAuth } from './useAuth';

/**
 * Tracks guest chat rate limiting: one message per player action.
 * Resets when the human turn ends (awaiting_action transitions true → false).
 */
export function useGuestChatLimit(
  isHumanTurn: boolean | undefined,
  handleSendMessage: (
    message: string,
    addressing?: string[],
    tone?: string,
    intensity?: string
  ) => Promise<void>
) {
  const { user } = useAuth();
  const isGuest = user?.is_guest ?? true;
  // PRH-27: free-text chat is sign-in-gated for guests (server enforces via
  // GUEST_FREE_CHAT_ENABLED, default off — typed text reaches the AI prompt
  // verbatim). The client locks conservatively for every guest; structured
  // quick-chat is gated separately. Non-guests are never affected.
  const guestFreeChatLocked = isGuest;
  const [guestChatSentThisAction, setGuestChatSentThisAction] = useState(false);

  const wasAwaitingAction = useRef(false);
  useEffect(() => {
    if (wasAwaitingAction.current && !isHumanTurn) {
      setGuestChatSentThisAction(false);
    }
    wasAwaitingAction.current = !!isHumanTurn;
  }, [isHumanTurn]);

  const wrappedSendMessage = useCallback(
    async (message: string, addressing?: string[], tone?: string, intensity?: string) => {
      try {
        await handleSendMessage(message, addressing, tone, intensity);
        if (isGuest) {
          setGuestChatSentThisAction(true);
        }
      } catch {
        // Don't mark as sent if the request failed
      }
    },
    [handleSendMessage, isGuest]
  );

  const guestChatDisabled = isGuest && (guestFreeChatLocked || guestChatSentThisAction);

  return { wrappedSendMessage, guestChatDisabled, guestFreeChatLocked, isGuest };
}
