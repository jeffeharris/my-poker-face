import { useState, useRef, useEffect, useMemo } from 'react';
import './ChatSidebar.css';

interface ChatMessage {
  id: string;
  sender: string;
  message: string;
  timestamp: string;
  type: 'game' | 'player' | 'system' | 'ai' | 'table';
}

interface ChatSidebarProps {
  messages: ChatMessage[];
  onSendMessage: (message: string) => void;
  playerName?: string;
}

// Available colors for players
const AVAILABLE_COLORS = [
  '#4caf50',    // Green
  '#9c27b0',    // Purple
  '#ff9800',    // Orange
  '#2196f3',    // Blue
  '#f44336',    // Red
  '#795548',    // Brown
  '#00bcd4',    // Cyan
  '#e91e63',    // Pink
];

type MessageFilter = 'all' | 'chat' | 'actions' | 'system';

export function ChatSidebar({ messages, onSendMessage, playerName = 'Player' }: ChatSidebarProps) {
  const [inputValue, setInputValue] = useState('');
  const [filter, setFilter] = useState<MessageFilter>('all');
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const playerColorsRef = useRef<Record<string, string>>({});
  const [colorUpdateTrigger, setColorUpdateTrigger] = useState(0);

  // Parse action messages to extract player and action
  const parseActionMessage = (message: string) => {
    // Match patterns like "Jeff chose to raise by $100" or "Jeff chose to call"
    const match = message.match(/^(.+?) chose to (\w+)(?:\s+(?:by\s+)?\$(\d+))?\.?$/);
    if (match) {
      return {
        player: match[1],
        action: match[2],
        amount: match[3] ? parseInt(match[3]) : null
      };
    }
    return null;
  };

  // Transform and filter messages
  const processedMessages = useMemo(() => {
    return messages
      .filter(msg => {
        // Filter out empty messages
        if (!msg.message || msg.message.trim() === '') return false;
        
        // Apply type filter
        switch (filter) {
          case 'chat':
            return msg.type === 'player' || msg.type === 'ai';
          case 'actions':
            return msg.sender.toLowerCase() === 'table' && msg.message.includes('chose to');
          case 'system':
            return msg.type === 'system' || msg.sender.toLowerCase() === 'system';
          default:
            return true;
        }
      })
      .map((msg) => {
        // Check if this message is a hand separator
        const isHandSeparator = msg.message.toLowerCase().includes('new hand dealt') ||
                               msg.message.toLowerCase().includes('new game started');
        
        // Transform action messages
        if (msg.sender.toLowerCase() === 'table' && msg.message.includes('chose to')) {
          const parsed = parseActionMessage(msg.message);
          if (parsed) {
            return {
              ...msg,
              displayType: 'action',
              parsedAction: parsed
            };
          }
        }
        
        // Mark hand separator messages
        if (isHandSeparator) {
          return {
            ...msg,
            displayType: 'separator'
          };
        }
        
        return { 
          ...msg, 
          displayType: msg.type
        };
      });
  }, [messages, filter]);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  useEffect(() => {
    scrollToBottom();
  }, [processedMessages]);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (inputValue.trim()) {
      onSendMessage(inputValue.trim());
      setInputValue('');
    }
  };

  const getMessageIcon = (type: string, sender?: string) => {
    switch (type) {
      case 'action':
        return '🎮';
      case 'game':
        return '🎲';
      case 'system':
        return '⚙️';
      case 'ai':
      case 'player':
        return sender === playerName ? '👤' : '💬';
      default:
        return '💬';
    }
  };

  const getPlayerColor = (name: string) => {
    if (!name || name.toLowerCase() === 'table' || name.toLowerCase() === 'system') return '#666666';
    
    // If player already has a color, return it
    if (playerColorsRef.current[name]) {
      return playerColorsRef.current[name];
    }
    
    // Assign a new color from available colors
    const usedColors = Object.values(playerColorsRef.current);
    const availableColor = AVAILABLE_COLORS.find(color => !usedColors.includes(color)) || 
                          AVAILABLE_COLORS[Object.keys(playerColorsRef.current).length % AVAILABLE_COLORS.length];
    
    playerColorsRef.current[name] = availableColor;
    setColorUpdateTrigger(prev => prev + 1); // Trigger re-render
    return availableColor;
  };

  const formatTime = (timestamp: string) => {
    try {
      const date = new Date(timestamp);
      return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    } catch {
      return '';
    }
  };

  const renderActionMessage = (msg: any) => {
    const { player, action, amount } = msg.parsedAction;
    const actionEmoji = {
      'fold': '🏳️',
      'check': '✅',
      'call': '📞',
      'raise': '📈',
      'all-in': '🚀',
      'all_in': '🚀'
    }[action.toLowerCase()] || '🎮';

    const actionText = {
      'fold': 'folded',
      'check': 'checked',
      'call': amount ? `called $${amount}` : 'called',
      'raise': amount ? `raised to $${amount}` : 'raised',
      'all-in': 'went all-in!',
      'all_in': 'went all-in!'
    }[action.toLowerCase()] || action;

    return (
      <div 
        key={msg.id}
        className="chat-message action-message"
        style={{ borderLeftColor: getPlayerColor(player) }}
      >
        <div className="action-content">
          <span className="action-emoji">{actionEmoji}</span>
          <span className="action-player" style={{ color: getPlayerColor(player) }}>
            {player}
          </span>
          <span className="action-text">{actionText}</span>
          <span className="message-time">{formatTime(msg.timestamp)}</span>
        </div>
      </div>
    );
  };

  return (
    <div className="chat-sidebar">
      <div className="chat-sidebar__header">
        <h3>Table Chat</h3>
        <div className="chat-filters">
          <button 
            className={`filter-btn ${filter === 'all' ? 'active' : ''}`}
            onClick={() => setFilter('all')}
            title="All messages"
          >
            🎯
          </button>
          <button 
            className={`filter-btn ${filter === 'chat' ? 'active' : ''}`}
            onClick={() => setFilter(filter === 'chat' ? 'all' : 'chat')}
            title="Chat only"
          >
            💬
          </button>
          <button 
            className={`filter-btn ${filter === 'actions' ? 'active' : ''}`}
            onClick={() => setFilter(filter === 'actions' ? 'all' : 'actions')}
            title="Actions only"
          >
            🎮
          </button>
          <button 
            className={`filter-btn ${filter === 'system' ? 'active' : ''}`}
            onClick={() => setFilter(filter === 'system' ? 'all' : 'system')}
            title="System messages"
          >
            🔔
          </button>
        </div>
      </div>
      
      <div className="chat-sidebar__messages">
        {processedMessages.length === 0 ? (
          <div className="chat-sidebar__empty">
            <p>No messages yet...</p>
            <p className="chat-sidebar__tip">Say hello to the table!</p>
          </div>
        ) : (
          processedMessages.map((msg: any) => {
            // Display as separator
            if (msg.displayType === 'separator') {
              return (
                <div key={msg.id} className="hand-separator">
                  <div className="separator-line" />
                  <span className="separator-text">{msg.message}</span>
                  <div className="separator-line" />
                </div>
              );
            }
            
            // Display as action message
            if (msg.displayType === 'action') {
              return renderActionMessage(msg);
            }
            
            // Display as regular message
            const isOwnMessage = msg.sender === playerName;
            const playerColor = getPlayerColor(msg.sender);

            return (
              <div 
                key={msg.id} 
                className={`chat-message ${msg.type} ${isOwnMessage ? 'own-message' : ''}`}
                style={{ 
                  borderLeftColor: msg.type === 'player' || msg.type === 'ai' 
                    ? playerColor 
                    : undefined 
                }}
              >
                <div className="message-header">
                  <span className="message-icon">{getMessageIcon(msg.type, msg.sender)}</span>
                  <span 
                    className="message-sender"
                    style={{ 
                      color: msg.type === 'player' || msg.type === 'ai' 
                        ? playerColor 
                        : undefined 
                    }}
                  >
                    {msg.sender}
                  </span>
                  <span className="message-time">{formatTime(msg.timestamp)}</span>
                </div>
                <div className="message-content">{msg.message}</div>
              </div>
            );
          })
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