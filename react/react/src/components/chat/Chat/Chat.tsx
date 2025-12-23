import { useState, useRef, useEffect } from 'react';
import type { ChatMessage } from '../../../types';
import './Chat.css';

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

  const getMessageIcon = (type: string, sender: string) => {
    switch (type) {
      case 'table':
        return '🎲';
      case 'system':
        return '⚙️';
      case 'ai':
        return '🤖';
      case 'player':
        return sender === playerName ? '👤' : '💬';
      default:
        return '💬';
    }
  };

  return (
    <>
      {/* Chat Toggle Button - Always visible */}
      <button 
        className={`chat-toggle ${isVisible ? 'chat-open' : 'chat-closed'}`} 
        onClick={onToggleVisibility}
      >
        💬 {isVisible ? 'Hide Chat' : `Chat (${messages.length})`}
      </button>

      {/* Chat Container */}
      <div className={`chat-container ${isVisible ? 'visible' : 'hidden'}`}>
        {isVisible && (
          <div className="chat-panel">
            {/* Chat Header */}
            <div className="chat-header">
              <h3>Game Chat</h3>
              <div className="chat-stats">
                {messages.length} messages
              </div>
            </div>

          {/* Messages */}
          <div className="chat-messages">
            {messages.length === 0 ? (
              <div className="no-messages">
                <p>No messages yet...</p>
                <p>💬 Say hello to start the conversation!</p>
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
                    {msg.message}
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