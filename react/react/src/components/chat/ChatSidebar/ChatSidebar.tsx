import { useState, useRef, useEffect, useMemo } from 'react';
import type { ChatMessage, Player } from '../../../types';
import { useFeatureFlags } from '../../debug/FeatureFlags';
import { QuickChatSuggestions } from '../QuickChatSuggestions';
import './ChatSidebar.css';

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
  const [selectedPlayer, setSelectedPlayer] = useState<string>('all');
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const playerColorsRef = useRef<Record<string, string>>({});
  const [colorUpdateTrigger, setColorUpdateTrigger] = useState(0);
  const [lastAction, setLastAction] = useState<any>(null);
  const featureFlags = useFeatureFlags();

  // Get all unique players from messages
  const allPlayers = useMemo(() => {
    const players = new Set<string>();
    messages.forEach(msg => {
      if (msg.sender && 
          msg.sender.toLowerCase() !== 'table' && 
          msg.sender.toLowerCase() !== 'system') {
        players.add(msg.sender);
      }
    });
    return Array.from(players).sort();
  }, [messages]);

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

  // Detect special event messages
  const detectEventType = (msg: ChatMessage) => {
    if (!msg.message) return null;
    
    const message = msg.message.toLowerCase();
    
    // Win detection
    if (message.includes('won') && message.includes('$')) {
      return 'win';
    }
    
    // All-in detection
    if (message.includes('all-in') || message.includes('all in')) {
      return 'all-in';
    }
    
    // Big pot detection (over $500)
    const potMatch = msg.message.match(/\$(\d+)/);
    if (potMatch && parseInt(potMatch[1]) >= 500) {
      return 'big-pot';
    }
    
    // Showdown/reveal detection
    if (message.includes('shows') || message.includes('revealed')) {
      return 'showdown';
    }
    
    // Elimination detection
    if (message.includes('eliminated') || message.includes('busted out')) {
      return 'elimination';
    }
    
    return null;
  };

  // Transform and filter messages
  const processedMessages = useMemo(() => {
    const filtered = messages
      .filter(msg => {
        // Filter out empty messages
        if (!msg.message || msg.message.trim() === '') return false;
        
        // Apply player filter if feature is enabled
        if (featureFlags.playerFilter && selectedPlayer !== 'all') {
          // For action messages, check if the player is mentioned
          if (msg.sender.toLowerCase() === 'table' && msg.message.includes('chose to')) {
            const parsed = parseActionMessage(msg.message);
            if (!parsed || parsed.player !== selectedPlayer) return false;
          } else {
            // For regular messages, check sender
            if (msg.sender !== selectedPlayer) return false;
          }
        }
        
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
        
        // Check for special events if feature is enabled
        const eventType = featureFlags.eventIndicators ? detectEventType(msg) : null;
        
        return { 
          ...msg, 
          displayType: msg.type,
          eventType
        };
      });
    
    // Apply message grouping if feature is enabled
    if (featureFlags.messageGrouping) {
      const grouped = filtered.map((msg, index) => {
        const prevMsg = index > 0 ? filtered[index - 1] : null;
        const nextMsg = index < filtered.length - 1 ? filtered[index + 1] : null;
        
        // For action messages, check the parsed player name
        const currentSender = msg.displayType === 'action' && msg.parsedAction 
          ? msg.parsedAction.player 
          : msg.sender;
        
        const prevSender = prevMsg && prevMsg.displayType === 'action' && prevMsg.parsedAction
          ? prevMsg.parsedAction.player
          : prevMsg?.sender;
          
        const nextSender = nextMsg && nextMsg.displayType === 'action' && nextMsg.parsedAction
          ? nextMsg.parsedAction.player
          : nextMsg?.sender;
        
        // Check if this message is part of a group
        const isFirstInGroup = !prevMsg || 
          prevSender !== currentSender || 
          prevMsg.displayType === 'separator' ||
          msg.displayType === 'separator';
          
        const isLastInGroup = !nextMsg || 
          nextSender !== currentSender || 
          nextMsg.displayType === 'separator' ||
          msg.displayType === 'separator';
        
        return {
          ...msg,
          isFirstInGroup,
          isLastInGroup,
          showHeader: isFirstInGroup
        };
      });
      
      return grouped;
    }
    
    return filtered;
  }, [messages, filter, selectedPlayer, featureFlags.playerFilter, featureFlags.messageGrouping, featureFlags.eventIndicators]);

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
          amount: parsed.amount
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

  const getMessageIcon = (type: string, sender?: string) => {
    switch (type) {
      case 'action':
        return '🎮';
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

  const renderActionMessage = (msg: any, index: number) => {
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

    const actionClasses = [
      'chat-message',
      'action-message',
      featureFlags.messageGrouping && !msg.isFirstInGroup ? 'grouped' : '',
      featureFlags.messageGrouping && !msg.isLastInGroup ? 'grouped-with-next' : ''
    ].filter(Boolean).join(' ');

    return (
      <div 
        key={`action-${index}-${msg.id}`}
        className={actionClasses}
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
        <div className="chat-filters-container">
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
          {featureFlags.playerFilter && allPlayers.length > 0 && (
            <>
              <div className="filter-divider">|</div>
              <select 
                className="player-filter-dropdown"
                value={selectedPlayer}
                onChange={(e) => setSelectedPlayer(e.target.value)}
                title="Filter by player"
              >
                <option value="all">👥 All Players</option>
                {allPlayers.map(player => (
                  <option key={player} value={player}>
                    {player}
                  </option>
                ))}
              </select>
            </>
          )}
        </div>
      </div>
      
      <div className="chat-sidebar__messages">
        {processedMessages.length === 0 ? (
          <div className="chat-sidebar__empty">
            <p>No messages yet...</p>
            <p className="chat-sidebar__tip">Say hello to the table!</p>
          </div>
        ) : (
          processedMessages.map((msg: any, index: number) => {
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
              isOwnMessage ? 'own-message' : '',
              featureFlags.messageGrouping && !msg.isFirstInGroup ? 'grouped' : '',
              featureFlags.messageGrouping && !msg.isLastInGroup ? 'grouped-with-next' : '',
              msg.eventType ? `event-${msg.eventType}` : ''
            ].filter(Boolean).join(' ');
            
            const eventEmoji = msg.eventType ? {
              'win': '🏆',
              'all-in': '📢',
              'big-pot': '💰',
              'showdown': '🎭',
              'elimination': '💀'
            }[msg.eventType] : null;

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
                {(!featureFlags.messageGrouping || msg.showHeader) && (
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
                )}
                <div className="message-content">
                  {eventEmoji && <span className="event-emoji">{eventEmoji}</span>}
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