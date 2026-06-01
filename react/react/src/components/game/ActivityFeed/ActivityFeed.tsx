import { useState, useRef, useEffect, useMemo, useCallback } from 'react';
import type { ChatMessage } from '../../../types';
import type { Player } from '../../../types/player';
import { parseMessageInline } from '../../../utils/messages';
import { QuickChatSuggestions } from '../../chat/QuickChatSuggestions';
import { ChatTargetSelector } from '../../chat/ChatTargetSelector';
import type { ChatTone, ChatIntensity } from '../../../types/chat';
import './ActivityFeed.css';

interface ActivityFeedProps {
  messages: ChatMessage[];
  /**
   * Widened to match the `wrappedSendMessage` / mobile signature so the same
   * function can be passed directly from PokerTable.  The extra params are only
   * used when a target / tone is selected; the existing call site that passes a
   * plain `(msg) => void` still compiles because the extra params are optional.
   */
  onSendMessage: (
    message: string,
    addressing?: string[],
    tone?: string,
    intensity?: string
  ) => void;
  playerName?: string;
  /** Locks the quick-chat surface (one send per guest turn). */
  guestChatDisabled?: boolean;
  /** Locks the free-text keyboard input for guests (PRH-27). NEW / OPTIONAL. */
  guestFreeChatLocked?: boolean;
  /**
   * All players in the current game — used to populate the target selector.
   * When absent the target selector is hidden. NEW / OPTIONAL.
   */
  players?: Player[];
  /**
   * Game ID forwarded to QuickChatSuggestions for the /api/chat/suggestions
   * fetch.  When absent the quick-chat panel degrades gracefully (the
   * component renders its collapsed toggle but won't fetch). NEW / OPTIONAL.
   */
  gameId?: string;
}

export function ActivityFeed({
  messages,
  onSendMessage,
  playerName = 'You',
  guestChatDisabled = false,
  guestFreeChatLocked = false,
  players,
  gameId,
}: ActivityFeedProps) {
  const [inputValue, setInputValue] = useState('');
  const [isInputExpanded, setIsInputExpanded] = useState(false);
  // Target for the free-text input. 'table' = broadcast (no addressing).
  const [textTarget, setTextTarget] = useState<string>('table');
  // Key bumped after each quick-chat send to remount the component fresh.
  const [quickChatKey, setQuickChatKey] = useState(0);
  const listRef = useRef<HTMLDivElement>(null);
  // Track whether the user is pinned to the bottom of the scroll container.
  // Default true so the first render lands at the latest message.
  const isAtBottomRef = useRef(true);

  // Derived: AI-only players for the target selector
  const aiPlayers = useMemo(() => (players ?? []).filter((p) => !p.is_human), [players]);

  // Parse action messages (e.g., "Batman chose to raise by $100")
  const parseActionMessage = useCallback((message: string) => {
    const match = message.match(/^(.+?) chose to (\w+)(?:\s+(?:by\s+)?\$(\d+))?\.?$/);
    if (match) {
      return {
        player: match[1],
        action: match[2],
        amount: match[3] ? parseInt(match[3]) : null,
      };
    }
    return null;
  }, []);

  // Format action for compact display
  const formatAction = useCallback((action: string, amount: number | null): string => {
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
  }, []);

  // Transform messages for activity feed (include all types)
  const activityItems = useMemo(
    () =>
      messages
        .slice(-50) // Keep last 50 for performance
        .map((msg) => {
          // Try to parse action from table messages (e.g., "Batman chose to raise by $100")
          const parsed = msg.type === 'table' ? parseActionMessage(msg.message) : null;
          return {
            ...msg,
            parsed,
          };
        }),
    [messages, parseActionMessage]
  );

  // Stick to bottom on new messages, but only if the user is already there.
  // If they've scrolled up to read history, leave their position alone.
  useEffect(() => {
    const el = listRef.current;
    if (!el || !isAtBottomRef.current) return;
    el.scrollTop = el.scrollHeight;
  }, [activityItems.length]);

  const handleListScroll = useCallback(() => {
    const el = listRef.current;
    if (!el) return;
    // 24px slack so trivial gaps still count as "at bottom"
    isAtBottomRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 24;
  }, []);

  const handleSend = useCallback(() => {
    const trimmed = inputValue.trim();
    if (!trimmed) return;
    if (textTarget && textTarget !== 'table') {
      onSendMessage(trimmed, [textTarget]);
    } else {
      onSendMessage(trimmed);
    }
    setInputValue('');
  }, [inputValue, textTarget, onSendMessage]);

  const handleQuickChatSelect = useCallback(
    (text: string, addressing?: string[], tone?: ChatTone, intensity?: ChatIntensity) => {
      onSendMessage(text, addressing, tone, intensity);
      setQuickChatKey((k) => k + 1);
    },
    [onSendMessage]
  );

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        handleSend();
      }
      if (e.key === 'Escape') {
        setIsInputExpanded(false);
      }
    },
    [handleSend]
  );

  return (
    <div className="activity-feed">
      <div className="activity-feed__header">
        <h3 className="activity-feed__title">Activity</h3>
      </div>

      <div className="activity-feed__list" ref={listRef} onScroll={handleListScroll}>
        {activityItems.length === 0 ? (
          <div className="activity-feed__empty">Waiting for action...</div>
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
                  <span className="activity-item__message">{parseMessageInline(item.message)}</span>
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
      </div>

      {/* Collapsible Chat Input */}
      <div className={`activity-feed__input ${isInputExpanded ? 'expanded' : ''}`}>
        {isInputExpanded ? (
          <div className="activity-feed__input-expanded">
            {/* Quick Chat suggestions — collapsed toggle by default in the sidebar */}
            {gameId && (
              <div className="activity-feed__quick-chat">
                <QuickChatSuggestions
                  key={quickChatKey}
                  gameId={gameId}
                  playerName={playerName}
                  players={players ?? []}
                  defaultExpanded={false}
                  hideHeader={false}
                  onSelectSuggestion={handleQuickChatSelect}
                  guestChatDisabled={guestChatDisabled}
                />
              </div>
            )}

            {/* Target selector for free-text — only when players are provided */}
            {aiPlayers.length > 0 && (
              <div className="activity-feed__target-selector">
                <ChatTargetSelector
                  aiPlayers={aiPlayers}
                  selectedTarget={textTarget}
                  onTargetSelect={setTextTarget}
                />
              </div>
            )}

            {/* Free-text input row */}
            <div className="activity-feed__input-container">
              <input
                type="text"
                value={inputValue}
                onChange={(e) => setInputValue(e.target.value)}
                onKeyDown={handleKeyDown}
                placeholder={
                  guestFreeChatLocked
                    ? 'Sign in with Google to chat'
                    : textTarget && textTarget !== 'table'
                      ? `Message ${textTarget}...`
                      : 'Say something...'
                }
                className="activity-feed__text-input"
                disabled={guestFreeChatLocked}
                autoFocus
                maxLength={200}
              />
              <button
                onClick={handleSend}
                disabled={!inputValue.trim() || guestFreeChatLocked}
                className="activity-feed__send-btn"
              >
                Send
              </button>
            </div>
          </div>
        ) : (
          <button onClick={() => setIsInputExpanded(true)} className="activity-feed__expand-btn">
            Chat...
          </button>
        )}
      </div>
    </div>
  );
}
