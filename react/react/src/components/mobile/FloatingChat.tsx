import { useEffect, useState } from 'react';
import type { ChatMessage } from '../../types';
import './FloatingChat.css';

interface FloatingChatProps {
  message: ChatMessage | null;
  onDismiss: () => void;
  duration?: number;
}

export function FloatingChat({ message, onDismiss, duration = 5000 }: FloatingChatProps) {
  const [isVisible, setIsVisible] = useState(false);
  const [isExiting, setIsExiting] = useState(false);

  useEffect(() => {
    if (message) {
      setIsVisible(true);
      setIsExiting(false);

      const timer = setTimeout(() => {
        setIsExiting(true);
        setTimeout(() => {
          setIsVisible(false);
          onDismiss();
        }, 300);
      }, duration);

      return () => clearTimeout(timer);
    }
  }, [message, duration, onDismiss]);

  if (!isVisible || !message) return null;

  const senderInitial = message.sender?.charAt(0).toUpperCase() || '?';
  const isAI = message.type === 'ai';

  return (
    <div
      className={`floating-chat ${isExiting ? 'exiting' : 'entering'}`}
      onClick={() => {
        setIsExiting(true);
        setTimeout(() => {
          setIsVisible(false);
          onDismiss();
        }, 300);
      }}
    >
      <div className="floating-chat-avatar">
        {senderInitial}
        {isAI && <span className="ai-badge">AI</span>}
      </div>
      <div className="floating-chat-content">
        <div className="floating-chat-sender">{message.sender}</div>
        <div className="floating-chat-message">{message.message}</div>
      </div>
      <div className="floating-chat-dismiss">×</div>
    </div>
  );
}
