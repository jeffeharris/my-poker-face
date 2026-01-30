import { useState, useRef, useEffect, type ReactNode } from 'react';
import { Users, Settings, Bot, User, MessageCircle } from 'lucide-react';
import type { ChatMessage } from '../../../types';
import { parseMessageBlock } from '../../../utils/messages';
import './Chat.css';

interface ChatProps {
  messages: ChatMessage[];
  onSendMessage: (message: string) => void;
  isVisible: boolean;
  onToggleVisibility: () => void;
  playerName?: string;
  guestChatDisabled?: boolean;
}

export function Chat({ messages, onSendMessage, isVisible, onToggleVisibility, playerName = 'Player', guestChatDisabled = false }: ChatProps) {
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
                    {parseMessageBlock(msg.message)}
                  </div>
                </div>
              ))
            )}
            <div ref={messagesEndRef} />
          </div>

          {/* Message Input */}
          <form className="chat-input-form" onSubmit={handleSendMessage}>
            {guestChatDisabled && (
              <div className="chat-disabled-notice">Chat available next turn</div>
            )}
            <div className="input-container">
              <input
                type="text"
                className="chat-input"
                placeholder={guestChatDisabled ? 'Chat available next turn' : 'Type a message...'}
                value={newMessage}
                onChange={(e) => setNewMessage(e.target.value)}
                maxLength={200}
                disabled={guestChatDisabled}
              />
              <button
                type="submit"
                className="send-button"
                disabled={!newMessage.trim() || guestChatDisabled}
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