import { useState, useRef, useEffect } from 'react';
import './ChatSidebar.css';

interface ChatMessage {
  id: string;
  sender: string;
  message: string;
  timestamp: string;
  type: 'game' | 'player' | 'system' | 'ai';
}

interface ChatSidebarProps {
  messages: ChatMessage[];
  onSendMessage: (message: string) => void;
  playerName?: string;
}

export function ChatSidebar({ messages, onSendMessage, playerName = 'Player' }: ChatSidebarProps) {
  const [inputValue, setInputValue] = useState('');
  const messagesEndRef = useRef<HTMLDivElement>(null);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages]);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (inputValue.trim()) {
      onSendMessage(inputValue.trim());
      setInputValue('');
    }
  };

  const getMessageIcon = (type: string, sender: string) => {
    switch (type) {
      case 'game':
        return '🎲';
      case 'system':
        return '⚙️';
      case 'ai':
        return '🤖';
      case 'player':
        return sender === playerName ? '👤' : '👥';
      default:
        return '💬';
    }
  };

  const formatTime = (timestamp: string) => {
    try {
      const date = new Date(timestamp);
      return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    } catch {
      return '';
    }
  };

  return (
    <div className="chat-sidebar">
      <div className="chat-sidebar__header">
        <h3>Table Chat</h3>
        <span className="chat-sidebar__count">{messages.length} messages</span>
      </div>
      
      <div className="chat-sidebar__messages">
        {messages.length === 0 ? (
          <div className="chat-sidebar__empty">
            <p>No messages yet...</p>
            <p className="chat-sidebar__tip">Say hello to the table!</p>
          </div>
        ) : (
          messages.map((msg) => (
            <div 
              key={msg.id} 
              className={`chat-message ${msg.type} ${msg.sender === playerName ? 'own-message' : ''}`}
            >
              <div className="message-header">
                <span className="message-icon">{getMessageIcon(msg.type, msg.sender)}</span>
                <span className="message-sender">{msg.sender}</span>
                <span className="message-time">{formatTime(msg.timestamp)}</span>
              </div>
              <div className="message-content">{msg.message}</div>
            </div>
          ))
        )}
        <div ref={messagesEndRef} />
      </div>
      
      <form className="chat-sidebar__input" onSubmit={handleSubmit}>
        <input
          type="text"
          value={inputValue}
          onChange={(e) => setInputValue(e.target.value)}
          placeholder="Type a message..."
          className="chat-input"
          maxLength={200}
        />
        <button 
          type="submit" 
          className="send-button"
          disabled={!inputValue.trim()}
        >
          Send
        </button>
      </form>
    </div>
  );
}