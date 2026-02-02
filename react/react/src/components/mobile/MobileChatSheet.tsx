import { useState, useRef, useEffect, useCallback } from 'react';
import { X, Keyboard, Zap, Send } from 'lucide-react';
import type { ChatMessage, WinResult } from '../../types';
import type { Player } from '../../types/player';
import { QuickChatSuggestions } from '../chat/QuickChatSuggestions';
import { parseMessageBlock } from '../../utils/messages';
import './MobileChatSheet.css';

type ParsedCard = { rank: string; suit: string; color: 'red' | 'white' };

function parseCard(raw: string): ParsedCard | null {
  const match = raw.match(/^(10|[2-9]|[AKQJ])([♠♥♦♣])$/);
  if (!match) return null;
  const [, rank, suit] = match;
  const color = suit === '♥' || suit === '♦' ? 'red' : 'white';
  return { rank, suit, color };
}

function parseCards(cards: string[]): ParsedCard[] {
  return cards.map(c => parseCard(c)).filter(Boolean) as ParsedCard[];
}

function renderCardChips(parsed: ParsedCard[]): React.ReactNode {
  return (
    <span className="mcs-card-row">
      {parsed.map((card, i) => (
        <span key={i} className={`mcs-card mcs-card-${card.color}`}>
          <span className="mcs-card-rank">{card.rank}</span>
          <span className="mcs-card-suit">{card.suit}</span>
        </span>
      ))}
    </span>
  );
}

function renderCardDeal(phase: string, cards: string[]): React.ReactNode {
  const label = phase.charAt(0).toUpperCase() + phase.slice(1);
  return (
    <span className="mcs-card-deal">
      <span className="mcs-card-phase">{label}</span>
      {renderCardChips(parseCards(cards))}
    </span>
  );
}

function renderCardRow(cards: string[]): React.ReactNode {
  return renderCardChips(parseCards(cards));
}

function renderWinResult(wr: WinResult): React.ReactNode {
  if (!wr.is_showdown) {
    // Fold-out: simple text
    return (
      <span className="mcs-win-result mcs-win-foldout">
        <span className="mcs-win-headline">
          <span className="mcs-win-player">{wr.winners}</span>
          {' took the pot of '}
          <span className="mcs-win-pot">${wr.pot}</span>
        </span>
      </span>
    );
  }

  // Showdown: rich display with cards
  return (
    <span className="mcs-win-result mcs-win-showdown">
      <span className="mcs-win-headline">
        <span className="mcs-win-player">{wr.winners}</span>
        {' won '}
        <span className="mcs-win-pot">${wr.pot}</span>
        {wr.hand_name && (
          <>
            {' with '}
            <span className="mcs-win-hand-name">{wr.hand_name}</span>
          </>
        )}
      </span>
      {wr.winner_cards && wr.winner_cards.length > 0 && (
        <span className="mcs-win-cards-row">
          <span className="mcs-win-label">Hand</span>
          {renderCardRow(wr.winner_cards)}
        </span>
      )}
      {wr.community_cards && wr.community_cards.length > 0 && (
        <span className="mcs-win-cards-row">
          <span className="mcs-win-label">Board</span>
          {renderCardRow(wr.community_cards)}
        </span>
      )}
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
  guestChatDisabled?: boolean;
  isGuest?: boolean;
}

export function MobileChatSheet({
  isOpen,
  onClose,
  messages,
  onSendMessage,
  gameId,
  playerName,
  players,
  guestChatDisabled = false,
  isGuest = false,
}: MobileChatSheetProps) {
  const [activeTab, setActiveTab] = useState<InputTab>(isGuest ? 'keyboard' : 'quick');
  const [inputValue, setInputValue] = useState('');
  const [isClosing, setIsClosing] = useState(false);
  const [quickChatKey, setQuickChatKey] = useState(0);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const messagesContainerRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const tabContentRef = useRef<HTMLDivElement>(null);
  const sheetRef = useRef<HTMLDivElement>(null);

  // Drag-to-dismiss state (refs to avoid re-renders during drag)
  const dragStartY = useRef(0);
  const dragCurrentY = useRef(0);
  const isDragging = useRef(false);
  const dragTimeoutRef = useRef<ReturnType<typeof setTimeout>>(undefined);
  const snapTimeoutRef = useRef<ReturnType<typeof setTimeout>>(undefined);

  // Track whether the sheet just opened vs already open
  const wasOpenRef = useRef(false);

  // Clean up drag timeouts on unmount
  useEffect(() => {
    return () => {
      clearTimeout(dragTimeoutRef.current);
      clearTimeout(snapTimeoutRef.current);
    };
  }, []);

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

  const handleDragStart = useCallback((e: React.TouchEvent) => {
    isDragging.current = true;
    dragStartY.current = e.touches[0].clientY;
    dragCurrentY.current = 0;
    if (sheetRef.current) {
      sheetRef.current.style.transition = 'none';
    }
  }, []);

  const handleDragMove = useCallback((e: React.TouchEvent) => {
    if (!isDragging.current) return;
    const delta = e.touches[0].clientY - dragStartY.current;
    // Only allow dragging downward
    dragCurrentY.current = Math.max(0, delta);
    if (sheetRef.current) {
      sheetRef.current.style.transform = `translateY(${dragCurrentY.current}px)`;
    }
  }, []);

  const handleDragEnd = useCallback(() => {
    if (!isDragging.current) return;
    isDragging.current = false;
    const sheet = sheetRef.current;
    if (!sheet) return;

    const threshold = sheet.offsetHeight * 0.3;
    if (dragCurrentY.current > threshold) {
      // Dismiss — animate out from current position
      sheet.style.transition = 'transform 0.25s ease-in';
      sheet.style.transform = 'translateY(100%)';
      dragTimeoutRef.current = setTimeout(() => {
        sheet.style.transition = '';
        sheet.style.transform = '';
        onClose();
      }, 250);
    } else {
      // Snap back
      sheet.style.transition = 'transform 0.2s ease-out';
      sheet.style.transform = 'translateY(0)';
      snapTimeoutRef.current = setTimeout(() => {
        sheet.style.transition = '';
      }, 200);
    }
  }, [onClose]);

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
        ref={sheetRef}
        className={`mcs-sheet ${isClosing ? 'mcs-sheet-closing' : ''}`}
        onClick={e => e.stopPropagation()}
      >
        {/* Header with drag handle at very top */}
        <div
          className="mcs-header"
          onTouchStart={handleDragStart}
          onTouchMove={handleDragMove}
          onTouchEnd={handleDragEnd}
        >
          <div className="mcs-drag-handle" />
          <div className="mcs-header-row">
            <h3 className="mcs-title">Chat</h3>
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
                  <div key={msg.id || `${msg.timestamp}-${msg.sender}-${i}`} className="mcs-hand-separator">
                    <span className="mcs-hand-separator-label">Game Start</span>
                  </div>
                );
              }
              if (msg.type === 'table' && msg.message.includes('NEW HAND DEALT')) {
                return (
                  <div key={msg.id || `${msg.timestamp}-${msg.sender}-${i}`} className="mcs-hand-separator">
                    <span className="mcs-hand-separator-label">New Hand</span>
                  </div>
                );
              }

              const isCardDeal = msg.type === 'table' && msg.phase && msg.cards;
              const isWinResult = msg.type === 'table' && msg.win_result;
              return (
                <div key={msg.id || `${msg.timestamp}-${msg.sender}-${i}`} className={`mcs-msg mcs-msg-${msg.type}${isCardDeal ? ' mcs-msg-card-deal' : ''}${isWinResult ? ' mcs-msg-win-result' : ''}`}>
                  {isWinResult ? (
                    <span className="mcs-msg-text">
                      {renderWinResult(msg.win_result!)}
                    </span>
                  ) : isCardDeal ? (
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
          <div className="mcs-tabs" role="tablist" aria-label="Chat input mode">
            <button
              role="tab"
              aria-selected={activeTab === 'quick'}
              aria-controls="mcs-tabpanel-quick"
              aria-label="Switch to Quick Chat mode"
              className={`mcs-tab ${activeTab === 'quick' ? 'mcs-tab-active' : ''} ${isGuest ? 'mcs-tab-disabled' : ''}`}
              onClick={() => !isGuest && setActiveTab('quick')}
              disabled={isGuest}
            >
              <Zap size={15} />
              <span>{isGuest ? 'Quick Chat (Sign in)' : 'Quick Chat'}</span>
            </button>
            <button
              role="tab"
              aria-selected={activeTab === 'keyboard'}
              aria-controls="mcs-tabpanel-keyboard"
              aria-label="Switch to keyboard input mode"
              className={`mcs-tab ${activeTab === 'keyboard' ? 'mcs-tab-active' : ''}`}
              onClick={() => setActiveTab('keyboard')}
            >
              <Keyboard size={15} />
              <span>Type</span>
            </button>
          </div>

          {/* Tab content */}
          <div className="mcs-tab-content" ref={tabContentRef} role="tabpanel" id={`mcs-tabpanel-${activeTab}`}>
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
                  guestChatDisabled={guestChatDisabled}
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
                  placeholder={guestChatDisabled ? 'Chat available next turn' : 'Say something...'}
                  value={inputValue}
                  onChange={e => setInputValue(e.target.value)}
                  onKeyDown={handleKeyDown}
                  maxLength={200}
                  disabled={guestChatDisabled}
                />
                <button
                  type="submit"
                  className={`mcs-send-btn ${inputValue.trim() ? 'mcs-send-active' : ''}`}
                  disabled={!inputValue.trim() || guestChatDisabled}
                  aria-label="Send message"
                >
                  <Send size={22} aria-hidden="true" />
                </button>
              </form>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
