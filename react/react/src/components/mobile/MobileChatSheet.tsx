import { useState, useRef, useEffect, useCallback } from 'react';
import { X, Keyboard, Zap, Send, ChevronDown } from 'lucide-react';
import type { ChatMessage } from '../../types';
import type { Player } from '../../types/player';
import { QuickChatSuggestions } from '../chat/QuickChatSuggestions';
import './MobileChatSheet.css';

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

  // Scroll to bottom when messages change or sheet opens
  useEffect(() => {
    if (isOpen && messagesEndRef.current) {
      setTimeout(() => {
        messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
      }, 100);
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

  const displayMessages = messages.slice(-80);

  return (
    <div
      className={`mcs-overlay ${isClosing ? 'mcs-closing' : ''}`}
      onClick={handleClose}
    >
      <div
        className={`mcs-sheet ${isClosing ? 'mcs-sheet-closing' : ''}`}
        onClick={e => e.stopPropagation()}
      >
        {/* Drag handle + header */}
        <div className="mcs-header">
          <div className="mcs-drag-handle" />
          <div className="mcs-header-row">
            <h3 className="mcs-title">Chat</h3>
            <button className="mcs-close-btn" onClick={handleClose} aria-label="Close chat">
              <ChevronDown size={22} />
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
            displayMessages.map((msg, i) => (
              <div key={msg.id || i} className={`mcs-msg mcs-msg-${msg.type}`}>
                <span className="mcs-msg-sender">{msg.sender}</span>
                <span className="mcs-msg-text">{msg.message}</span>
              </div>
            ))
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
                  <Send size={18} />
                </button>
              </form>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
