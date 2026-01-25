import React, { useState, useRef, useEffect, type ReactNode } from 'react';
import { Users, Settings, Bot, User, MessageCircle } from 'lucide-react';
import type { ChatMessage } from '../../../types';
import './Chat.css';

// Parse message with dramatic sequence support
// Splits on newlines (beats) and renders actions (*asterisks*) as italics
function parseMessage(text: string): React.ReactNode {
  // Split on newlines (beats) and process each
  const beats = text.split('\n').filter(b => b.trim());

  if (beats.length === 0) {
    return text;
  }

  return beats.map((beat, i) => {
    // Check if it's an action (*wrapped in asterisks*)
    const actionMatch = beat.match(/^\*(.+)\*$/);
    if (actionMatch) {
      return <div key={i} className="beat action"><em>{actionMatch[1]}</em></div>;
    }
    return <div key={i} className="beat speech">{beat}</div>;
  });
}

interface ChatProps {
  messages: ChatMessage[];
  onSendMessage: (message: string) => void;
  isVisible: boolean;
  onToggleVisibility: () => void;
  playerName?: string;
}

export function Chat({ messages, onSendMessage, isVisible, onToggleVisibility, playerName = 'Player' }: ChatProps) {
  const [newMessage, setNewMessage] = useState('');
  const messagesEndRef = useRef<HTMLDivElement>(null);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages]);

  const handleSendMessage = (e: React.FormEvent) => {
    e.preventDefault();
    if (newMessage.trim()) {
      onSendMessage(newMessage.trim());
      setNewMessage('');
    }
  };

  const formatTimestamp = (timestamp: string) => {
    return new Date(timestamp).toLocaleTimeString([], {
      hour: '2-digit',
      minute: '2-digit'
    });
  };

  const getMessageIcon = (type: string, sender: string): ReactNode => {
    const iconProps = { size: 14, className: "message-type-icon" };
    switch (type) {
      case 'table':
        return <Users {...iconProps} />;
      case 'system':
        return <Settings {...iconProps} />;
      case 'ai':
        return <Bot {...iconProps} />;
      case 'player':
        return sender === playerName ? <User {...iconProps} /> : <MessageCircle {...iconProps} />;
      default:
        return <MessageCircle {...iconProps} />;
    }
  };

  return (
    <>
      {/* Chat Container */}
      <div className={`chat-container ${isVisible ? 'visible' : 'hidden'}`}>
        {isVisible && (
          <div className="chat-panel">
            {/* Chat Header */}
            <div className="chat-sheet-header">
              <h3>Table Chat</h3>
              <button onClick={onToggleVisibility}>×</button>
            </div>

          {/* Messages */}
          <div className="chat-messages">
            {messages.length === 0 ? (
              <div className="no-messages">
                <p>No messages yet...</p>
                <p>Say hello to start the conversation!</p>
              </div>
            ) : (
              messages.map((msg) => (
                <div 
                  key={msg.id} 
                  className={`chat-message ${msg.type} ${msg.sender === playerName ? 'own-message' : ''}`}
                >
                  <div className="message-header">
                    <span className="message-icon">
                      {getMessageIcon(msg.type, msg.sender)}
                    </span>
                    <span className="message-sender">{msg.sender}</span>
                    <span className="message-time">
                      {formatTimestamp(msg.timestamp)}
                    </span>
                  </div>
                  <div className="message-content">
                    {parseMessage(msg.message)}
                  </div>
                </div>
              ))
            )}
            <div ref={messagesEndRef} />
          </div>

          {/* Message Input */}
          <form className="chat-input-form" onSubmit={handleSendMessage}>
            <div className="input-container">
              <input
                type="text"
                className="chat-input"
                placeholder="Type a message..."
                value={newMessage}
                onChange={(e) => setNewMessage(e.target.value)}
                maxLength={200}
              />
              <button 
                type="submit" 
                className="send-button"
                disabled={!newMessage.trim()}
              >
                Send
              </button>
            </div>
          </form>
          </div>
        )}
      </div>
    </>
  );
}