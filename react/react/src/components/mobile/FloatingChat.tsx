import { useEffect, useState, useRef } from 'react';
import type { ChatMessage, Player } from '../../types';
import { config } from '../../config';
import './FloatingChat.css';

interface MessageWithMeta extends ChatMessage {
  addedAt: number;
  isExiting: boolean;
}

interface FloatingChatProps {
  message: ChatMessage | null;
  onDismiss: () => void;
  duration?: number;
  players?: Player[];
}

export function FloatingChat({ message, onDismiss, duration = 8000, players = [] }: FloatingChatProps) {
  const [messages, setMessages] = useState<MessageWithMeta[]>([]);
  const processedIdsRef = useRef<Set<string>>(new Set());

  // Get avatar URL for a player by name
  const getPlayerAvatar = (senderName: string): string | null => {
    const player = players.find(p => p.name === senderName);
    return player?.avatar_url ? `${config.API_URL}${player.avatar_url}` : null;
  };

  // Add new message to stack when it arrives
  useEffect(() => {
    if (message && !processedIdsRef.current.has(message.id)) {
      processedIdsRef.current.add(message.id);
      setMessages(prev => [...prev, {
        ...message,
        addedAt: Date.now(),
        isExiting: false
      }]);
    }
  }, [message]);

  // Handle TTL expiration for each message
  useEffect(() => {
    if (messages.length === 0) return;

    const checkExpired = () => {
      const now = Date.now();

      setMessages(prev => {
        let changed = false;
        const updated = prev.map(msg => {
          const elapsed = now - msg.addedAt;
          if (elapsed >= duration && !msg.isExiting) {
            changed = true;
            return { ...msg, isExiting: true };
          }
          return msg;
        });

        // Remove messages that finished exit animation (300ms)
        const filtered = updated.filter(msg => {
          if (!msg.isExiting) return true;
          const elapsed = now - msg.addedAt;
          return elapsed < duration + 300;
        });

        if (filtered.length !== updated.length) changed = true;

        // Call onDismiss when all messages are gone
        if (filtered.length === 0 && prev.length > 0) {
          processedIdsRef.current.clear();
          onDismiss();
        }

        return changed ? filtered : prev;
      });
    };

    // Calculate delay to next expiration instead of polling every 100ms
    const now = Date.now();
    let nextDelay = duration; // Default to full duration

    for (const msg of messages) {
      const elapsed = now - msg.addedAt;
      const targetDuration = msg.isExiting ? duration + 300 : duration;
      const remaining = targetDuration - elapsed;
      if (remaining > 0 && remaining < nextDelay) {
        nextDelay = remaining;
      } else if (remaining <= 0) {
        // Message already expired, check immediately
        nextDelay = 0;
        break;
      }
    }

    const timer = setTimeout(checkExpired, Math.max(0, nextDelay));
    return () => clearTimeout(timer);
  }, [messages, duration, onDismiss]);

  const handleDismiss = (id: string) => {
    setMessages(prev =>
      prev.map(msg => msg.id === id ? { ...msg, isExiting: true } : msg)
    );
    setTimeout(() => {
      setMessages(prev => prev.filter(msg => msg.id !== id));
    }, 300);
  };

  if (messages.length === 0) return null;

  return (
    <div className="floating-chat-stack">
      {messages.map((msg) => {
        const senderInitial = msg.sender?.charAt(0).toUpperCase() || '?';
        const isAI = msg.type === 'ai';

        const avatarUrl = isAI ? getPlayerAvatar(msg.sender || '') : null;

        return (
          <div
            key={msg.id}
            className={`floating-chat ${msg.isExiting ? 'exiting' : 'entering'}`}
            onClick={() => handleDismiss(msg.id)}
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
              <div className="floating-chat-message">{msg.message}</div>
            </div>
            <div className="floating-chat-dismiss">×</div>
          </div>
        );
      })}
    </div>
  );
}
