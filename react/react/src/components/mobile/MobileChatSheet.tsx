import { useState, useRef, useEffect, useCallback } from 'react';
import { X, Keyboard, Zap, Send } from 'lucide-react';
import type { ChatMessage } from '../../types';
import type { Player } from '../../types/player';
import { QuickChatSuggestions } from '../chat/QuickChatSuggestions';
import { parseMessageBlock } from '../../utils/messages';
import './MobileChatSheet.css';

/** Parse a card string like "A♠" or "10♥" into { rank, suit, color } */
function parseCard(raw: string): { rank: string; suit: string; color: 'red' | 'white' } | null {
  const match = raw.match(/^(\d{1,2}|[AKQJ])([♠♥♦♣])$/);
  if (!match) return null;
  const [, rank, suit] = match;
  const color = suit === '♥' || suit === '♦' ? 'red' : 'white';
  return { rank, suit, color };
}

/** Render structured card-deal data as formatted card chips */
function renderCardDeal(phase: string, cards: string[]): React.ReactNode {
  const parsed = cards.map(c => parseCard(c)).filter(Boolean) as { rank: string; suit: string; color: 'red' | 'white' }[];
  const label = phase.charAt(0).toUpperCase() + phase.slice(1);

  return (
    <span className="mcs-card-deal">
      <span className="mcs-card-phase">{label}</span>
      <span className="mcs-card-row">
        {parsed.map((card, i) => (
          <span key={i} className={`mcs-card mcs-card-${card.color}`}>
            <span className="mcs-card-rank">{card.rank}</span>
            <span className="mcs-card-suit">{card.suit}</span>
          </span>
        ))}
      </span>
    </span>
  );
}

type InputTab = 'quick' | 'keyboard';

interface MobileChatSheetProps {
  isOpen: boolean;
  onClose: () => void;
  messages: ChatMessage[];
  onSendMessage: (message: string) => void;
  gameId: string;
  playerName: string;
  players: Player[];
}

export function MobileChatSheet({
  isOpen,
  onClose,
  messages,
  onSendMessage,
  gameId,
  playerName,
  players,
}: MobileChatSheetProps) {
  const [activeTab, setActiveTab] = useState<InputTab>('quick');
  const [inputValue, setInputValue] = useState('');
  const [isClosing, setIsClosing] = useState(false);
  const [quickChatKey, setQuickChatKey] = useState(0);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const messagesContainerRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const tabContentRef = useRef<HTMLDivElement>(null);

  // Track whether the sheet just opened vs already open
  const wasOpenRef = useRef(false);

  // Scroll to bottom: instant on open, smooth for new messages
  useEffect(() => {
    if (isOpen && messagesEndRef.current) {
      const justOpened = !wasOpenRef.current;
      wasOpenRef.current = true;
      setTimeout(() => {
        messagesEndRef.current?.scrollIntoView({
          behavior: justOpened ? 'instant' : 'smooth',
        });
      }, justOpened ? 0 : 100);
    }
    if (!isOpen) {
      wasOpenRef.current = false;
    }
  }, [isOpen, messages.length]);

  // Focus input when switching to keyboard tab
  useEffect(() => {
    if (activeTab === 'keyboard' && isOpen) {
      setTimeout(() => inputRef.current?.focus(), 150);
    }
  }, [activeTab, isOpen]);

  // Scroll tab content to bottom when quick chat suggestions load
  const handleSuggestionsLoaded = useCallback(() => {
    setTimeout(() => {
      tabContentRef.current?.scrollTo({
        top: tabContentRef.current.scrollHeight,
        behavior: 'smooth',
      });
    }, 50);
  }, []);

  const handleClose = () => {
    setIsClosing(true);
    setTimeout(() => {
      setIsClosing(false);
      onClose();
    }, 250);
  };

  const handleSend = () => {
    const trimmed = inputValue.trim();
    if (trimmed) {
      onSendMessage(trimmed);
      setInputValue('');
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const handleQuickChatSelect = (text: string) => {
    onSendMessage(text);
    // Remount QuickChatSuggestions so it resets to expanded with fresh state
    setQuickChatKey(k => k + 1);
  };

  if (!isOpen) return null;

  const displayMessages = messages
    .filter(msg => msg.type !== 'system')
    .slice(-80);

  return (
    <div
      className={`mcs-overlay ${isClosing ? 'mcs-closing' : ''}`}
      onClick={handleClose}
    >
      <div
        className={`mcs-sheet ${isClosing ? 'mcs-sheet-closing' : ''}`}
        onClick={e => e.stopPropagation()}
      >
        {/* Header with drag handle centered in row */}
        <div className="mcs-header">
          <div className="mcs-header-row">
            <h3 className="mcs-title">Chat</h3>
            <div className="mcs-drag-handle" />
            <button className="mcs-close-btn" onClick={handleClose} aria-label="Close chat">
              <X size={20} />
            </button>
          </div>
        </div>

        {/* Messages area - always visible, takes remaining space */}
        <div className="mcs-messages" ref={messagesContainerRef}>
          {displayMessages.length === 0 ? (
            <div className="mcs-empty">
              <span className="mcs-empty-text">No messages yet. Start the conversation!</span>
            </div>
          ) : (
            displayMessages.map((msg, i) => {
              // Render hand/game markers as visual separators
              if (msg.type === 'table' && msg.message.includes('GAME START')) {
                return (
                  <div key={msg.id || i} className="mcs-hand-separator">
                    <span className="mcs-hand-separator-label">Game Start</span>
                  </div>
                );
              }
              if (msg.type === 'table' && msg.message.includes('NEW HAND DEALT')) {
                return (
                  <div key={msg.id || i} className="mcs-hand-separator">
                    <span className="mcs-hand-separator-label">New Hand</span>
                  </div>
                );
              }

              const isCardDeal = msg.type === 'table' && msg.phase && msg.cards;
              return (
                <div key={msg.id || i} className={`mcs-msg mcs-msg-${msg.type}${isCardDeal ? ' mcs-msg-card-deal' : ''}`}>
                  {isCardDeal ? (
                    <span className="mcs-msg-text">
                      {renderCardDeal(msg.phase!, msg.cards!)}
                    </span>
                  ) : msg.type === 'table' ? (
                    <span className="mcs-msg-text">{msg.message}</span>
                  ) : (
                    <>
                      <span className="mcs-msg-sender">{msg.sender}</span>
                      <span className="mcs-msg-text">{parseMessageBlock(msg.message)}</span>
                    </>
                  )}
                </div>
              );
            })
          )}
          <div ref={messagesEndRef} />
        </div>

        {/* Input area with tabs */}
        <div className="mcs-input-area">
          {/* Tab switcher */}
          <div className="mcs-tabs">
            <button
              className={`mcs-tab ${activeTab === 'quick' ? 'mcs-tab-active' : ''}`}
              onClick={() => setActiveTab('quick')}
            >
              <Zap size={15} />
              <span>Quick Chat</span>
            </button>
            <button
              className={`mcs-tab ${activeTab === 'keyboard' ? 'mcs-tab-active' : ''}`}
              onClick={() => setActiveTab('keyboard')}
            >
              <Keyboard size={15} />
              <span>Type</span>
            </button>
          </div>

          {/* Tab content */}
          <div className="mcs-tab-content" ref={tabContentRef}>
            {activeTab === 'quick' ? (
              <div className="mcs-quick-chat-wrapper">
                <QuickChatSuggestions
                  key={quickChatKey}
                  gameId={gameId}
                  playerName={playerName}
                  players={players}
                  defaultExpanded={true}
                  hideHeader={true}
                  onSelectSuggestion={handleQuickChatSelect}
                  onSuggestionsLoaded={handleSuggestionsLoaded}
                />
              </div>
            ) : (
              <form
                className="mcs-keyboard-input"
                onSubmit={e => {
                  e.preventDefault();
                  handleSend();
                }}
              >
                <input
                  ref={inputRef}
                  type="text"
                  className="mcs-text-input"
                  placeholder="Say something..."
                  value={inputValue}
                  onChange={e => setInputValue(e.target.value)}
                  onKeyDown={handleKeyDown}
                  maxLength={200}
                />
                <button
                  type="submit"
                  className={`mcs-send-btn ${inputValue.trim() ? 'mcs-send-active' : ''}`}
                  disabled={!inputValue.trim()}
                >
                  <Send size={22} />
                </button>
              </form>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
