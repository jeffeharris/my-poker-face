import { memo, useEffect, useState, useRef, forwardRef } from 'react';
import { motion, AnimatePresence, useMotionValue, useTransform, animate } from 'framer-motion';
import type { PanInfo } from 'framer-motion';
import type { ChatMessage } from '../../types';
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

// Swipe-to-dismiss thresholds. The opacity ramp is anchored to the
// trailing edge's screen position (see useTransform below); these
// raw-pixel checks only gate when the gesture commits.
const SWIPE_DISMISS_DISTANCE = 110;
const SWIPE_DISMISS_VELOCITY = 500;
// Minimum opacity during the swipe — keeps the bubble visible while
// it's sliding off so the user sees the message leaving rather than
// a phantom translation of empty space.
const SWIPE_OPACITY_FLOOR = 0.3;
// Duration of the off-screen slide animation that runs after the
// user commits the swipe (release past threshold). Short enough that
// the bubble doesn't linger; long enough that the user perceives a
// distinct "it slid away" rather than a teleport.
const SWIPE_OFFSCREEN_DURATION = 0.22;

interface MessageWithMeta extends ChatMessage {
  addedAt: number;
  displayDuration: number;
  timerStartedAt: number | null; // null = timer paused (not in visible zone yet)
}

interface FloatingChatProps {
  message: ChatMessage | null;
  onDismiss: () => void;
  playerAvatars?: Map<string, string>;
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

// Message component — swipe horizontally to dismiss, or wait for the
// auto-dismiss timer. No X button (the swipe gesture + timer
// together cover the dismiss surface).
interface MessageItemProps {
  msg: MessageWithMeta;
  avatarUrl: string | null;
  onDismiss: (id: string) => void;
}

const MessageItem = forwardRef<HTMLDivElement, MessageItemProps>(function MessageItem({ msg, avatarUrl, onDismiss }, ref) {
  const senderInitial = msg.sender?.charAt(0).toUpperCase() || '?';
  const isAI = msg.type === 'ai';

  // Horizontal drag offset tracked as a MotionValue so the fade
  // opacity below can derive from it without triggering React
  // re-renders on every pointer move — the transform updates run
  // on the compositor.
  const x = useMotionValue(0);

  // Snapshot the bubble's resting rect on mount so the fade can be
  // anchored to the trailing edge's screen-X position rather than a
  // fixed-pixel swipe distance. Drag is short-lived; we don't track
  // resize during the gesture (worst case: one off-curve frame
  // after a rotation before release).
  const innerRef = useRef<HTMLDivElement>(null);
  const restRectRef = useRef<{ left: number; right: number; viewportWidth: number } | null>(null);
  useEffect(() => {
    const el = innerRef.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    restRectRef.current = {
      left: rect.left,
      right: rect.right,
      viewportWidth: window.innerWidth,
    };
  }, []);

  // Cubic ease-in fade anchored to the trailing edge's journey
  // toward the matching screen edge — a left swipe fades as the
  // right edge approaches the left screen edge, and vice versa.
  // Clamped at SWIPE_OPACITY_FLOOR so the bubble stays visible
  // while it's sliding off rather than becoming a phantom empty
  // box translating across the screen.
  const dragOpacity = useTransform(x, (latest) => {
    const rect = restRectRef.current;
    if (!rect || latest === 0) return 1;
    let progress: number;
    if (latest < 0) {
      progress = -latest / rect.right;
    } else {
      progress = latest / Math.max(1, rect.viewportWidth - rect.left);
    }
    progress = Math.min(Math.max(progress, 0), 1);
    return Math.max(SWIPE_OPACITY_FLOOR, 1 - progress * progress * progress);
  });

  const handleDragEnd = (
    _e: MouseEvent | TouchEvent | PointerEvent,
    info: PanInfo,
  ) => {
    const distance = Math.abs(info.offset.x);
    const velocity = Math.abs(info.velocity.x);
    if (distance > SWIPE_DISMISS_DISTANCE || velocity > SWIPE_DISMISS_VELOCITY) {
      // Continue the slide all the way off-screen in the direction
      // the user committed to, then unmount. Without this the bubble
      // would freeze mid-swipe and AnimatePresence's center-fade
      // exit would feel disconnected from the gesture.
      const direction = (info.offset.x || info.velocity.x) < 0 ? -1 : 1;
      // 100px buffer past the viewport edge so the bubble fully
      // clears any rounded shadows / blurs before unmount.
      const target = direction * (window.innerWidth + 100);
      animate(x, target, {
        type: 'tween',
        duration: SWIPE_OFFSCREEN_DURATION,
        ease: 'easeOut',
        onComplete: () => onDismiss(msg.id),
      });
      return;
    }
    // Below threshold → snap back. Spring matches the stack's
    // layout transitions so the bubble settles with the same feel.
    animate(x, 0, { type: 'spring', stiffness: 500, damping: 35 });
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
      className="floating-chat-swipe-wrap"
    >
      <motion.div
        ref={innerRef}
        drag="x"
        // Drag handlers belong on the inner element so the outer
        // can continue to own the enter/exit + `layout` transitions
        // unimpeded. `dragMomentum=false` keeps a flick from
        // coasting past the threshold visually before the dismiss
        // commits via animate().
        dragMomentum={false}
        onDragEnd={handleDragEnd}
        style={{ x, opacity: dragOpacity }}
        className="floating-chat"
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
            </div>
          )}
        </div>
      </motion.div>
    </motion.div>
  );
});

// How many messages can have active timers (visible zone)
const ACTIVE_MESSAGE_LIMIT = 2;

export const FloatingChat = memo(function FloatingChat({ message, onDismiss, playerAvatars }: FloatingChatProps) {
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
            />
          );
        })}
      </AnimatePresence>
    </div>
  );
});
