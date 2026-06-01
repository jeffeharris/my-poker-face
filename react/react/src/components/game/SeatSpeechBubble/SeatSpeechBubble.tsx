import { memo, useEffect } from 'react';
import { motion } from 'framer-motion';
import type { ChatMessage } from '../../../types';
import { DramaticMessage } from '../../mobile/FloatingChat';
import { calculateDuration } from '../../../utils/chatBeats';
import './SeatSpeechBubble.css';

interface SeatSpeechBubbleProps {
  /** The AI message to show. The bubble is rendered inside the speaking
   *  opponent's seat, so the caller already gates on sender === player. */
  message: ChatMessage;
  /** Clear the active message (auto-dismiss timer or click). */
  onDismiss: () => void;
}

/**
 * Desktop chat bubble that pops up beneath the seat of the opponent who spoke.
 * Reuses the FloatingChat typewriter beats, but anchored to the seat (it's
 * rendered as an absolutely-positioned child of the arc seat) and auto-dismissed
 * on a content-length timer — no swipe gesture (that's a mobile affordance).
 */
export const SeatSpeechBubble = memo(function SeatSpeechBubble({
  message,
  onDismiss,
}: SeatSpeechBubbleProps) {
  // Auto-dismiss after a duration scaled to the message length, matching the
  // floating-chat reading cadence. Re-armed whenever the message changes.
  useEffect(() => {
    const duration = calculateDuration(message.message, message.action);
    const timer = setTimeout(onDismiss, duration);
    return () => clearTimeout(timer);
  }, [message.id, message.message, message.action, onDismiss]);

  return (
    <motion.div
      key={message.id}
      className="seat-speech-bubble"
      // x: '-50%' keeps the horizontal centering inside Framer's transform —
      // a CSS `translateX(-50%)` would be clobbered by the animated scale/y.
      initial={{ opacity: 0, x: '-50%', y: -6, scale: 0.92 }}
      animate={{ opacity: 1, x: '-50%', y: 0, scale: 1 }}
      exit={{ opacity: 0, x: '-50%', y: -6, scale: 0.92, transition: { duration: 0.18 } }}
      transition={{ type: 'spring', stiffness: 500, damping: 32 }}
      onClick={onDismiss}
      role="status"
      data-testid="seat-speech-bubble"
    >
      {(message.action || message.sender) && (
        <div className="seat-speech-bubble__sender">{message.action || message.sender}</div>
      )}
      {message.message && (
        <div className="seat-speech-bubble__text">
          <DramaticMessage text={message.message} />
        </div>
      )}
    </motion.div>
  );
});
