import { memo, useEffect, useState, useRef, forwardRef } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import type { ChatMessage, ReactionSentiment } from '../../types';
import { ReactionButtons, ReactionChips } from '../chat/MessageReactions/MessageReactions';
import {
  TYPING_SPEED_MS,
  READING_BUFFER_MS,
  ACTION_FADE_DURATION_MS,
  BEAT_DELAY_MS,
  QUEUED_MESSAGE_BONUS_MS,
  MESSAGE_BASE_DURATION_MS,
  MESSAGE_MIN_DURATION_MS,
  MESSAGE_MAX_DURATION_MS,
} from '../../config/timing';
import './FloatingChat.css';

interface MessageWithMeta extends ChatMessage {
  addedAt: number;
  displayDuration: number;
  timerStartedAt: number | null; // null = timer paused (not in visible zone yet)
}

interface FloatingChatProps {
  message: ChatMessage | null;
  onDismiss: () => void;
  playerAvatars?: Map<string, string>;
  /** Player display name for highlighting the active sentiment. */
  playerName?: string;
  /** When provided, AI bubbles show the 2-button reaction stack. */
  onSendReaction?: (messageId: string, sentiment: ReactionSentiment | null) => void;
}

// Parse a beat to determine if it's an action or speech
interface ParsedBeat {
  type: 'action' | 'speech';
  text: string;
}

function parseBeats(text: string): ParsedBeat[] {
  const lines = text.split('\n').filter(b => b.trim());
  return lines.map(line => {
    const actionMatch = line.match(/^\*(.+)\*$/);
    if (actionMatch) {
      return { type: 'action', text: actionMatch[1] };
    }
    return { type: 'speech', text: line };
  });
}

// Calculate display duration based on message content and timing
function calculateDuration(message: string, action?: string): number {
  const trimmedMessage = message.trim();
  const trimmedAction = action?.trim() ?? '';
  const text = trimmedMessage.length > 0 ? trimmedMessage : trimmedAction;

  if (!text) return MESSAGE_MIN_DURATION_MS;

  const beats = parseBeats(text);
  let animationTime = 0;

  beats.forEach((beat, i) => {
    // Add beat delay (except for first beat)
    if (i > 0) animationTime += BEAT_DELAY_MS;

    if (beat.type === 'action') {
      animationTime += ACTION_FADE_DURATION_MS + beat.text.length * READING_BUFFER_MS;
    } else {
      // Typing time for speech + reading buffer
      animationTime += beat.text.length * (TYPING_SPEED_MS + READING_BUFFER_MS);
    }
  });

  const calculated = animationTime + MESSAGE_BASE_DURATION_MS;
  return Math.min(MESSAGE_MAX_DURATION_MS, Math.max(MESSAGE_MIN_DURATION_MS, calculated));
}

// Action beat component - fades in
function ActionBeat({ text, delay }: { text: string; delay: number }) {
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    const timer = setTimeout(() => setVisible(true), delay);
    return () => clearTimeout(timer);
  }, [delay]);

  return (
    <div className={`beat action ${visible ? 'visible' : ''}`}>
      <em>{text}</em>
    </div>
  );
}

// Speech beat component - types out character by character
function SpeechBeat({ text, delay }: { text: string; delay: number }) {
  const [displayedText, setDisplayedText] = useState('');
  const [started, setStarted] = useState(false);

  useEffect(() => {
    const startTimer = setTimeout(() => setStarted(true), delay);
    return () => clearTimeout(startTimer);
  }, [delay]);

  useEffect(() => {
    if (!started) return;

    let charIndex = 0;
    const interval = setInterval(() => {
      if (charIndex < text.length) {
        setDisplayedText(text.slice(0, charIndex + 1));
        charIndex++;
      } else {
        clearInterval(interval);
      }
    }, TYPING_SPEED_MS);

    return () => clearInterval(interval);
  }, [started, text]);

  if (!started) return null;

  return (
    <div className="beat speech">
      {displayedText}
      {displayedText.length < text.length && <span className="typing-cursor">|</span>}
    </div>
  );
}

// Dramatic message component - orchestrates beat animations
function DramaticMessage({ text }: { text: string }) {
  const beats = parseBeats(text);

  if (beats.length === 0) {
    return <>{text}</>;
  }

  // Calculate cumulative delays for each beat
  let cumulativeDelay = 0;
  const beatsWithDelay = beats.map((beat, i) => {
    const delay = cumulativeDelay;

    // Calculate how long this beat takes
    if (beat.type === 'action') {
      cumulativeDelay += ACTION_FADE_DURATION_MS + BEAT_DELAY_MS;
    } else {
      cumulativeDelay += (beat.text.length * TYPING_SPEED_MS) + BEAT_DELAY_MS;
    }

    return { ...beat, delay, index: i };
  });

  return (
    <>
      {beatsWithDelay.map((beat) => (
        beat.type === 'action' ? (
          <ActionBeat key={beat.index} text={beat.text} delay={beat.delay} />
        ) : (
          <SpeechBeat key={beat.index} text={beat.text} delay={beat.delay} />
        )
      ))}
    </>
  );
}

// Message component - only X button dismisses
interface MessageItemProps {
  msg: MessageWithMeta;
  avatarUrl: string | null;
  onDismiss: (id: string) => void;
  playerName?: string;
  onSendReaction?: (messageId: string, sentiment: ReactionSentiment | null) => void;
  /** Called when the user taps a reaction button — gives the parent
   * a chance to keep the bubble visible long enough to see the
   * result rather than letting the dismiss timer cut it off. */
  onReactionInteract?: (id: string) => void;
}

const MessageItem = forwardRef<HTMLDivElement, MessageItemProps>(function MessageItem(
  { msg, avatarUrl, onDismiss, playerName, onSendReaction, onReactionInteract },
  ref,
) {
  const senderInitial = msg.sender?.charAt(0).toUpperCase() || '?';
  const isAI = msg.type === 'ai';
  const canReact = isAI && !!onSendReaction && !!msg.id;

  const handleReact = (messageId: string, sentiment: ReactionSentiment | null) => {
    onReactionInteract?.(messageId);
    onSendReaction!(messageId, sentiment);
  };

  return (
    <motion.div
      ref={ref}
      layout
      initial={{ opacity: 0, y: -20, scale: 0.95 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      exit={{
        opacity: 0,
        scale: 0.95,
        y: -10,
        transition: { duration: 0.2 }
      }}
      transition={{
        layout: { type: "spring", stiffness: 500, damping: 35 },
        opacity: { duration: 0.2 },
        scale: { duration: 0.25 },
        y: { type: "spring", stiffness: 500, damping: 35 }
      }}
      className={`floating-chat${canReact ? ' has-reactions' : ''}`}
      data-testid="floating-chat"
    >
      <div className={`floating-chat-avatar ${avatarUrl ? 'has-image' : ''}`} data-testid="floating-chat-avatar">
        {avatarUrl ? (
          <img src={avatarUrl} alt={msg.sender} className="floating-avatar-img" />
        ) : (
          senderInitial
        )}
        {isAI && !avatarUrl && <span className="ai-badge">AI</span>}
      </div>
      <div className="floating-chat-content">
        <div className="floating-chat-sender" data-testid="floating-chat-sender">
          {msg.action || msg.sender}
        </div>
        {msg.message && (
          <div className="floating-chat-message" data-testid="floating-chat-message">
            <DramaticMessage text={msg.message} />
            {isAI && <ReactionChips reactions={msg.reactions} />}
          </div>
        )}
      </div>
      {canReact && msg.id && (
        <ReactionButtons
          messageId={msg.id}
          reactions={msg.reactions}
          playerName={playerName || ''}
          onReact={handleReact}
          variant="floating"
        />
      )}
      <button
        className="floating-chat-dismiss"
        data-testid="floating-chat-dismiss"
        onClick={(e) => {
          e.stopPropagation();
          onDismiss(msg.id);
        }}
        aria-label="Dismiss"
      >
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
          <path d="M18 6L6 18M6 6l12 12" />
        </svg>
      </button>
    </motion.div>
  );
});

// How many messages can have active timers (visible zone)
const ACTIVE_MESSAGE_LIMIT = 2;

// How long to keep a bubble alive after the user taps a reaction —
// gives them a beat to see the rolled emoji + chip update before the
// dismiss timer cuts the bubble off mid-feedback. Picked at the lower
// end of MESSAGE_MAX_DURATION so spamming reactions can't pin a
// bubble open indefinitely.
const REACTION_INTERACTION_BONUS_MS = 3000;

export const FloatingChat = memo(function FloatingChat({
  message,
  onDismiss,
  playerAvatars,
  playerName,
  onSendReaction,
}: FloatingChatProps) {
  const [messages, setMessages] = useState<MessageWithMeta[]>([]);
  const processedIdsRef = useRef<Set<string>>(new Set());
  // Keep a ref to current messages for timer callbacks (avoids stale closures)
  const messagesRef = useRef<MessageWithMeta[]>([]);
  messagesRef.current = messages;

  const getPlayerAvatar = (senderName: string): string | null => {
    return playerAvatars?.get(senderName) ?? null;
  };

  // Add new message to stack
  useEffect(() => {
    if (message && !processedIdsRef.current.has(message.id)) {
      processedIdsRef.current.add(message.id);
      const msgDuration = calculateDuration(message.message, message.action);

      setMessages(prev => {
        const newPosition = prev.length;
        const isInActiveZone = newPosition < ACTIVE_MESSAGE_LIMIT;

        return [...prev, {
          ...message,
          addedAt: Date.now(),
          displayDuration: msgDuration,
          timerStartedAt: isInActiveZone ? Date.now() : null
        }];
      });
    }
  }, [message]);

  // Merge updates to an already-tracked message's `reactions` field
  // — the dedupe-on-add path above never re-applies prop changes
  // after first sighting, so without this effect a reaction landing
  // while the bubble is visible would leave the chip count stale.
  // Timer state (addedAt, displayDuration, timerStartedAt) is
  // preserved unchanged; only the chat fields are refreshed.
  useEffect(() => {
    if (!message || !processedIdsRef.current.has(message.id)) return;
    setMessages(prev => {
      let changed = false;
      const next = prev.map(m => {
        if (m.id !== message.id) return m;
        if (m.reactions === message.reactions) return m;
        changed = true;
        return { ...m, reactions: message.reactions };
      });
      return changed ? next : prev;
    });
  }, [message]);

  // Activate timers for messages that moved into the visible zone
  useEffect(() => {
    setMessages(prev => {
      let changed = false;
      const updated = prev.map((msg, index) => {
        // If message is now in active zone but timer hasn't started, start it
        if (index < ACTIVE_MESSAGE_LIMIT && msg.timerStartedAt === null) {
          changed = true;
          // Add bonus time for messages that were waiting in queue
          return {
            ...msg,
            timerStartedAt: Date.now(),
            displayDuration: msg.displayDuration + QUEUED_MESSAGE_BONUS_MS
          };
        }
        return msg;
      });
      return changed ? updated : prev;
    });
  }, [messages.length]); // Re-check when message count changes

  // When all messages are cleared, notify parent
  useEffect(() => {
    if (messages.length === 0 && processedIdsRef.current.size > 0) {
      processedIdsRef.current.clear();
      onDismiss();
    }
  }, [messages.length, onDismiss]);

  // Handle TTL expiration
  useEffect(() => {
    if (messages.length === 0) return;

    const checkExpired = () => {
      const now = Date.now();
      setMessages(prev => {
        const filtered = prev.filter(msg => {
          // If timer hasn't started, message is paused - keep it
          if (msg.timerStartedAt === null) return true;
          const elapsed = now - msg.timerStartedAt;
          return elapsed < msg.displayDuration;
        });

        return filtered.length !== prev.length ? filtered : prev;
      });
    };

    // Calculate delay to next expiration using ref (avoids stale closure)
    const now = Date.now();
    let nextDelay = 15000;

    for (const msg of messagesRef.current) {
      if (msg.timerStartedAt === null) continue; // Skip paused messages
      const remaining = msg.displayDuration - (now - msg.timerStartedAt);
      if (remaining > 0 && remaining < nextDelay) {
        nextDelay = remaining;
      } else if (remaining <= 0) {
        nextDelay = 0;
        break;
      }
    }

    const timer = setTimeout(checkExpired, Math.max(0, nextDelay));
    return () => clearTimeout(timer);
  }, [messages.length]);

  const handleDismiss = (id: string) => {
    setMessages(prev => prev.filter(msg => msg.id !== id));
  };

  // Extend the bubble's display window when the user taps a reaction
  // so the rolled emoji + chip update has a moment to land. We push
  // `timerStartedAt` forward in time (and bump displayDuration so the
  // newly-pushed-out deadline still respects the bonus window) rather
  // than mutating displayDuration alone, which would let an old start
  // time race past the new deadline immediately on slow renders.
  const handleReactionInteract = (id: string) => {
    setMessages(prev => prev.map(msg => {
      if (msg.id !== id || msg.timerStartedAt === null) return msg;
      const elapsed = Date.now() - msg.timerStartedAt;
      const remaining = msg.displayDuration - elapsed;
      // Only extend if the current window is shorter than the bonus —
      // otherwise we'd accidentally shrink a bubble that was already
      // going to stay longer than the bonus.
      if (remaining >= REACTION_INTERACTION_BONUS_MS) return msg;
      return {
        ...msg,
        timerStartedAt: Date.now(),
        displayDuration: REACTION_INTERACTION_BONUS_MS,
      };
    }));
  };

  if (messages.length === 0) return null;

  return (
    <div className="floating-chat-stack" data-testid="floating-chat-stack">
      <AnimatePresence mode="popLayout">
        {messages.map((msg) => {
          const isAI = msg.type === 'ai';
          const avatarUrl = isAI ? getPlayerAvatar(msg.sender || '') : null;

          return (
            <MessageItem
              key={msg.id}
              msg={msg}
              avatarUrl={avatarUrl}
              onDismiss={handleDismiss}
              playerName={playerName}
              onSendReaction={onSendReaction}
              onReactionInteract={handleReactionInteract}
            />
          );
        })}
      </AnimatePresence>
    </div>
  );
});
