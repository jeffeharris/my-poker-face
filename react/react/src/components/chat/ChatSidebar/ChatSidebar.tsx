import { useState, useRef, useEffect, useMemo, type ReactNode } from 'react';
import {
  Target, MessageCircle, Gamepad2, Bell, Users, Settings, Bot, User,
  Flag, CheckCircle, Phone, TrendingUp, Rocket
} from 'lucide-react';
import type { ChatMessage, Player } from '../../../types';
import { QuickChatSuggestions } from '../QuickChatSuggestions';
import { config } from '../../../config';
import './ChatSidebar.css';

// Type for parsed action messages (e.g., "Jeff chose to raise by $100")
interface ParsedAction {
  player: string;
  action: string;
  amount: number | null;
}

// Type for tracked last action
interface LastAction {
  type: string;
  player: string;
  amount?: number;
}

// Extended message type with display properties added during processing
interface ProcessedChatMessage extends ChatMessage {
  displayType: 'action' | 'separator' | ChatMessage['type'];
  parsedAction?: ParsedAction;
}

interface ChatSidebarProps {
  messages: ChatMessage[];
  onSendMessage: (message: string) => void;
  playerName?: string;
  gameId?: string;
  players?: Player[];
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

export function ChatSidebar({ messages, onSendMessage, playerName = 'Player', gameId, players = [] }: ChatSidebarProps) {
  const [inputValue, setInputValue] = useState('');
  const [filter, setFilter] = useState<MessageFilter>('all');
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const playerColorsRef = useRef<Record<string, string>>({});
  const [, setColorUpdateTrigger] = useState(0);
  const [lastAction, setLastAction] = useState<LastAction | undefined>(undefined);

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
  const processedMessages = useMemo((): ProcessedChatMessage[] => {
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
        const isHandSeparator = msg.message && (msg.message.toLowerCase().includes('new hand dealt') ||
                               msg.message.toLowerCase().includes('new game started'));

        // Transform action messages
        if (msg.sender && msg.message && msg.sender.toLowerCase() === 'table' && msg.message.includes('chose to')) {
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

  // Track last action
  useEffect(() => {
    const lastActionMessage = messages
      .filter(msg => msg.sender && msg.message && msg.sender.toLowerCase() === 'table' && msg.message.includes('chose to'))
      .pop();
    
    if (lastActionMessage) {
      const parsed = parseActionMessage(lastActionMessage.message);
      if (parsed) {
        setLastAction({
          type: parsed.action,
          player: parsed.player,
          amount: parsed.amount ?? undefined
        });
      }
    }
  }, [messages]);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (inputValue.trim()) {
      onSendMessage(inputValue.trim());
      setInputValue('');
    }
  };

  const getMessageIcon = (type: string, sender?: string): ReactNode => {
    const iconProps = { size: 14, className: "message-type-icon" };
    switch (type) {
      case 'action':
        return <Gamepad2 {...iconProps} />;
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

  // Get avatar URL for a player by name
  const getPlayerAvatar = (senderName: string): string | null => {
    const player = players.find(p => p.name === senderName);
    return player?.avatar_url ? `${config.API_URL}${player.avatar_url}` : null;
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

  const getActionIcon = (actionKey: string): ReactNode => {
    const iconProps = { size: 14, className: "action-icon" };
    switch (actionKey) {
      case 'fold': return <Flag {...iconProps} />;
      case 'check': return <CheckCircle {...iconProps} />;
      case 'call': return <Phone {...iconProps} />;
      case 'raise': return <TrendingUp {...iconProps} />;
      case 'all-in':
      case 'all_in': return <Rocket {...iconProps} />;
      default: return <Gamepad2 {...iconProps} />;
    }
  };

  const renderActionMessage = (msg: ProcessedChatMessage, index: number) => {
    if (!msg.parsedAction) return null;
    const { player, action, amount } = msg.parsedAction;
    const actionKey = action.toLowerCase();
    const actionIcon = getActionIcon(actionKey);

    const actionTextMap = {
      'fold': 'folded',
      'check': 'checked',
      'call': amount ? `called $${amount}` : 'called',
      'raise': amount ? `raised to $${amount}` : 'raised',
      'all-in': 'went all-in!',
      'all_in': 'went all-in!'
    } as Record<string, string>;
    const actionText = actionTextMap[actionKey] || action;

    const actionClasses = 'chat-message action-message';

    return (
      <div 
        key={`action-${index}-${msg.id}`}
        className={actionClasses}
        style={{ borderLeftColor: getPlayerColor(player) }}
      >
        <div className="action-content">
          <span className="action-emoji">{actionIcon}</span>
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
        <div className="chat-filters-container">
          <div className="chat-filters">
          <button
            className={`filter-btn ${filter === 'all' ? 'active' : ''}`}
            onClick={() => setFilter('all')}
            title="All messages"
          >
            <Target size={14} />
          </button>
          <button
            className={`filter-btn ${filter === 'chat' ? 'active' : ''}`}
            onClick={() => setFilter(filter === 'chat' ? 'all' : 'chat')}
            title="Chat only"
          >
            <MessageCircle size={14} />
          </button>
          <button
            className={`filter-btn ${filter === 'actions' ? 'active' : ''}`}
            onClick={() => setFilter(filter === 'actions' ? 'all' : 'actions')}
            title="Actions only"
          >
            <Gamepad2 size={14} />
          </button>
          <button
            className={`filter-btn ${filter === 'system' ? 'active' : ''}`}
            onClick={() => setFilter(filter === 'system' ? 'all' : 'system')}
            title="System messages"
          >
            <Bell size={14} />
          </button>
          </div>
        </div>
      </div>
      
      <div className="chat-sidebar__messages">
        {processedMessages.length === 0 ? (
          <div className="chat-sidebar__empty">
            <p>No messages yet...</p>
            <p className="chat-sidebar__tip">Say hello to the table!</p>
          </div>
        ) : (
          processedMessages.map((msg, index) => {
            // Display as separator
            if (msg.displayType === 'separator') {
              return (
                <div key={`sep-${index}-${msg.id}`} className="hand-separator">
                  <div className="separator-line" />
                  <span className="separator-text">{msg.message}</span>
                  <div className="separator-line" />
                </div>
              );
            }
            
            // Display as action message
            if (msg.displayType === 'action') {
              return renderActionMessage(msg, index);
            }
            
            // Display as regular message
            const isOwnMessage = msg.sender === playerName;
            const playerColor = getPlayerColor(msg.sender);

            const messageClasses = [
              'chat-message',
              msg.type,
              isOwnMessage ? 'own-message' : ''
            ].filter(Boolean).join(' ');

            return (
              <div
                key={`msg-${index}-${msg.id}`}
                className={messageClasses}
                style={{
                  borderLeftColor: msg.type === 'player' || msg.type === 'ai'
                    ? playerColor
                    : undefined
                }}
              >
                <div className="message-header">
                  {msg.type === 'ai' && getPlayerAvatar(msg.sender) ? (
                    <img
                      src={getPlayerAvatar(msg.sender)!}
                      alt={msg.sender}
                      className="chat-avatar"
                    />
                  ) : (
                    <span className="message-icon">{getMessageIcon(msg.type, msg.sender)}</span>
                  )}
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
                <div className="message-content">
                  {msg.message}
                </div>
              </div>
            );
          })
        )}
        <div ref={messagesEndRef} />
      </div>
      
      {gameId && players.length > 0 && (
        <QuickChatSuggestions
          gameId={gameId}
          playerName={playerName}
          players={players}
          lastAction={lastAction}
          onSelectSuggestion={(text) => {
            setInputValue(text);
          }}
        />
      )}
      
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