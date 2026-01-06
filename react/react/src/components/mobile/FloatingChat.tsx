import { useEffect, useState, useRef } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import type { ChatMessage, Player } from '../../types';
import { config } from '../../config';
import './FloatingChat.css';

interface MessageWithMeta extends ChatMessage {
  addedAt: number;
  displayDuration: number;
  timerStartedAt: number | null; // null = timer paused (not in visible zone yet)
}

interface FloatingChatProps {
  message: ChatMessage | null;
  onDismiss: () => void;
  duration?: number;
  players?: Player[];
}

// Calculate display duration based on message length
// Base: 3 seconds, plus 50ms per character, capped between 3-15 seconds
function calculateDuration(message: string, action?: string): number {
  const baseMs = 3000;
  const msPerChar = 50;
  const minMs = 3000;
  const maxMs = 15000;
  // Prefer non-empty message text; if message is empty/whitespace, fall back to action text
  const trimmedMessage = message.trim();
  const trimmedAction = action?.trim() ?? '';
  const textLength = trimmedMessage.length > 0 ? trimmedMessage.length : trimmedAction.length;
  const calculated = baseMs + (textLength * msPerChar);
  return Math.min(maxMs, Math.max(minMs, calculated));
}

// Message component - only X button dismisses
interface MessageItemProps {
  msg: MessageWithMeta;
  avatarUrl: string | null;
  onDismiss: (id: string) => void;
}

function MessageItem({ msg, avatarUrl, onDismiss }: MessageItemProps) {
  const senderInitial = msg.sender?.charAt(0).toUpperCase() || '?';
  const isAI = msg.type === 'ai';

  return (
    <motion.div
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
      className="floating-chat"
    >
      <div className={`floating-chat-avatar ${avatarUrl ? 'has-image' : ''}`}>
        {avatarUrl ? (
          <img src={avatarUrl} alt={msg.sender} className="floating-avatar-img" />
        ) : (
          senderInitial
        )}
        {isAI && !avatarUrl && <span className="ai-badge">AI</span>}
      </div>
      <div className="floating-chat-content">
        <div className="floating-chat-sender">
          {msg.action || msg.sender}
        </div>
        {msg.message && (
          <div className="floating-chat-message">{msg.message}</div>
        )}
      </div>
      <button
        className="floating-chat-dismiss"
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
}

// How many messages can have active timers (visible zone)
const ACTIVE_MESSAGE_LIMIT = 2;

export function FloatingChat({ message, onDismiss, duration = 8000, players = [] }: FloatingChatProps) {
  const [messages, setMessages] = useState<MessageWithMeta[]>([]);
  const processedIdsRef = useRef<Set<string>>(new Set());

  void duration; // Using per-message duration instead

  const getPlayerAvatar = (senderName: string): string | null => {
    const player = players.find(p => p.name === senderName);
    return player?.avatar_url ? `${config.API_URL}${player.avatar_url}` : null;
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
          return { ...msg, timerStartedAt: Date.now() };
        }
        return msg;
      });
      return changed ? updated : prev;
    });
  }, [messages.length]); // Re-check when message count changes

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

        if (filtered.length === 0 && prev.length > 0) {
          processedIdsRef.current.clear();
          onDismiss();
        }

        return filtered.length !== prev.length ? filtered : prev;
      });
    };

    // Calculate delay to next expiration (only for active timers)
    const now = Date.now();
    let nextDelay = 15000;

    for (const msg of messages) {
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
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [messages.length, onDismiss]);

  const handleDismiss = (id: string) => {
    setMessages(prev => prev.filter(msg => msg.id !== id));
  };

  if (messages.length === 0) return null;

  return (
    <div className="floating-chat-stack">
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
}
