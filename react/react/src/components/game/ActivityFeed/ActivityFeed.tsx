import { useState, useRef, useEffect } from 'react';
import type { ChatMessage, Player } from '../../../types';
import './ActivityFeed.css';

interface ActivityFeedProps {
  messages: ChatMessage[];
  onSendMessage: (message: string) => void;
  players: Player[];
  playerName?: string;
}

export function ActivityFeed({
  messages,
  onSendMessage,
  players,
  playerName = 'You',
}: ActivityFeedProps) {
  const [inputValue, setInputValue] = useState('');
  const [isInputExpanded, setIsInputExpanded] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  // Parse action messages (e.g., "Batman chose to raise by $100")
  const parseActionMessage = (message: string) => {
    const match = message.match(/^(.+?) chose to (\w+)(?:\s+(?:by\s+)?\$(\d+))?\.?$/);
    if (match) {
      return {
        player: match[1],
        action: match[2],
        amount: match[3] ? parseInt(match[3]) : null,
      };
    }
    return null;
  };

  // Format action for compact display
  const formatAction = (action: string, amount: number | null): string => {
    const actionVerbs: Record<string, string> = {
      fold: 'folds',
      check: 'checks',
      call: 'calls',
      bet: 'bets',
      raise: 'raises',
      all_in: 'goes all-in',
    };
    const verb = actionVerbs[action.toLowerCase()] || action;
    return amount ? `${verb} $${amount}` : verb;
  };

  // Transform messages for activity feed (include all types)
  const activityItems = messages
    .slice(-50) // Keep last 50 for performance
    .map(msg => {
      // Try to parse action from table messages (e.g., "Batman chose to raise by $100")
      const parsed = msg.type === 'table' ? parseActionMessage(msg.message) : null;
      return {
        ...msg,
        parsed,
      };
    });

  // Auto-scroll to bottom when new messages arrive
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [activityItems.length]);

  const handleSend = () => {
    if (inputValue.trim()) {
      onSendMessage(inputValue.trim());
      setInputValue('');
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
    if (e.key === 'Escape') {
      setIsInputExpanded(false);
    }
  };

  return (
    <div className="activity-feed">
      <div className="activity-feed__header">
        <h3 className="activity-feed__title">Activity</h3>
      </div>

      <div className="activity-feed__list">
        {activityItems.length === 0 ? (
          <div className="activity-feed__empty">
            Waiting for action...
          </div>
        ) : (
          activityItems.map((item, idx) => {
            // Parsed action from table message
            if (item.parsed) {
              return (
                <div key={item.id || idx} className="activity-item action">
                  <span className="activity-item__player">{item.parsed.player}</span>
                  <span className="activity-item__action">
                    {formatAction(item.parsed.action, item.parsed.amount)}
                  </span>
                </div>
              );
            }

            // Player chat message
            if (item.type === 'player') {
              return (
                <div key={item.id || idx} className="activity-item chat player-chat">
                  <span className="activity-item__sender">{item.sender}:</span>
                  <span className="activity-item__message">{item.message}</span>
                </div>
              );
            }

            // AI chat message
            if (item.type === 'ai') {
              return (
                <div key={item.id || idx} className="activity-item chat ai-chat">
                  <span className="activity-item__sender">{item.sender}:</span>
                  <span className="activity-item__message">{item.message}</span>
                </div>
              );
            }

            // Table announcement (non-action)
            if (item.type === 'table') {
              return (
                <div key={item.id || idx} className="activity-item table">
                  <span className="activity-item__message">{item.message}</span>
                </div>
              );
            }

            // System message
            return (
              <div key={item.id || idx} className="activity-item system">
                <span className="activity-item__message">{item.message}</span>
              </div>
            );
          })
        )}
        <div ref={messagesEndRef} />
      </div>

      {/* Collapsible Chat Input */}
      <div className={`activity-feed__input ${isInputExpanded ? 'expanded' : ''}`}>
        {isInputExpanded ? (
          <div className="activity-feed__input-container">
            <input
              type="text"
              value={inputValue}
              onChange={(e) => setInputValue(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="Say something..."
              className="activity-feed__text-input"
              autoFocus
            />
            <button
              onClick={handleSend}
              disabled={!inputValue.trim()}
              className="activity-feed__send-btn"
            >
              Send
            </button>
          </div>
        ) : (
          <button
            onClick={() => setIsInputExpanded(true)}
            className="activity-feed__expand-btn"
          >
            Chat...
          </button>
        )}
      </div>
    </div>
  );
}
