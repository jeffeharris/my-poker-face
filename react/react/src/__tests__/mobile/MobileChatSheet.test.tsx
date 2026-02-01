import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, act } from '@testing-library/react';
import { MobileChatSheet } from '../../components/mobile/MobileChatSheet';
import type { ChatMessage } from '../../types/chat';
import type { Player } from '../../types/player';

// jsdom doesn't implement scrollIntoView
Element.prototype.scrollIntoView = vi.fn();

// Mock QuickChatSuggestions to avoid network calls and complex dependencies
vi.mock('../../components/chat/QuickChatSuggestions', () => ({
  QuickChatSuggestions: ({ guestChatDisabled }: { guestChatDisabled?: boolean }) => (
    <div data-testid="quick-chat-suggestions">
      {guestChatDisabled ? 'Chat disabled' : 'Quick Chat Suggestions'}
    </div>
  ),
}));

// Mock parseMessageBlock to return plain text
vi.mock('../../utils/messages', () => ({
  parseMessageBlock: (text: string) => text,
}));

const mockPlayers: Player[] = [
  {
    name: 'TestPlayer',
    stack: 2000,
    bet: 50,
    is_folded: false,
    is_all_in: false,
    is_human: true,
  },
  {
    name: 'Batman',
    stack: 1975,
    bet: 25,
    is_folded: false,
    is_all_in: false,
    is_human: false,
    avatar_url: '/avatars/batman.png',
  },
];

function makeMessage(overrides: Partial<ChatMessage> = {}): ChatMessage {
  return {
    id: `msg-${Math.random()}`,
    sender: 'Batman',
    message: 'I am the night.',
    timestamp: new Date().toISOString(),
    type: 'ai',
    ...overrides,
  };
}

function makeProps(overrides: Partial<Parameters<typeof MobileChatSheet>[0]> = {}) {
  return {
    isOpen: true,
    onClose: vi.fn(),
    messages: [] as ChatMessage[],
    onSendMessage: vi.fn(),
    gameId: 'test-game',
    playerName: 'TestPlayer',
    players: mockPlayers,
    guestChatDisabled: false,
    isGuest: false,
    ...overrides,
  };
}

describe('VT-03: MobileChatSheet — tabs, messages, guest restrictions', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe('Case 1: Empty messages', () => {
    it('shows "No messages yet" text when no messages', () => {
      render(<MobileChatSheet {...makeProps({ messages: [] })} />);
      expect(screen.getByText(/No messages yet/)).toBeTruthy();
    });
  });

  describe('Case 2: With messages', () => {
    it('renders message items with sender and text', () => {
      const messages: ChatMessage[] = [
        makeMessage({ id: 'msg-1', sender: 'Batman', message: 'I am the night.', type: 'ai' }),
        makeMessage({ id: 'msg-2', sender: 'TestPlayer', message: 'Nice hand!', type: 'player' }),
      ];
      render(<MobileChatSheet {...makeProps({ messages })} />);

      expect(screen.getByText('Batman')).toBeTruthy();
      expect(screen.getByText('I am the night.')).toBeTruthy();
      expect(screen.getByText('TestPlayer')).toBeTruthy();
      expect(screen.getByText('Nice hand!')).toBeTruthy();
    });

    it('filters out system messages', () => {
      const messages: ChatMessage[] = [
        makeMessage({ id: 'msg-1', sender: 'Batman', message: 'Hello', type: 'ai' }),
        makeMessage({ id: 'msg-2', sender: 'System', message: 'System event', type: 'system' }),
      ];
      render(<MobileChatSheet {...makeProps({ messages })} />);

      expect(screen.getByText('Hello')).toBeTruthy();
      expect(screen.queryByText('System event')).toBeNull();
    });

    it('shows max 80 messages', () => {
      const messages: ChatMessage[] = Array.from({ length: 100 }, (_, i) =>
        makeMessage({ id: `msg-${i}`, sender: 'Batman', message: `Message ${i}`, type: 'ai' })
      );
      const { container } = render(<MobileChatSheet {...makeProps({ messages })} />);

      // Should show the last 80 messages (indices 20-99)
      const msgElements = container.querySelectorAll('.mcs-msg');
      expect(msgElements.length).toBe(80);

      // First 20 messages should be excluded
      expect(screen.queryByText('Message 0')).toBeNull();
      expect(screen.queryByText('Message 19')).toBeNull();

      // Last messages should be visible
      expect(screen.getByText('Message 99')).toBeTruthy();
      expect(screen.getByText('Message 20')).toBeTruthy();
    });
  });

  describe('Case 3: Guest user', () => {
    it('disables Quick Chat tab and shows "Sign in" text', () => {
      render(<MobileChatSheet {...makeProps({ isGuest: true })} />);

      expect(screen.getByText(/Quick Chat \(Sign in\)/)).toBeTruthy();

      // The quick chat tab should be disabled
      const quickTab = screen.getByRole('tab', { name: /Quick Chat/i });
      expect(quickTab).toHaveProperty('disabled', true);
    });

    it('defaults to keyboard tab for guests', () => {
      render(<MobileChatSheet {...makeProps({ isGuest: true })} />);

      // Keyboard tab should be active — text input should be visible
      const input = document.querySelector('.mcs-text-input') as HTMLInputElement;
      expect(input).toBeTruthy();
    });
  });

  describe('Case 4: Tab switching', () => {
    it('keyboard tab shows text input and send button', () => {
      render(<MobileChatSheet {...makeProps()} />);

      // Switch to keyboard tab
      const keyboardTab = screen.getByRole('tab', { name: /keyboard/i });
      fireEvent.click(keyboardTab);

      const input = document.querySelector('.mcs-text-input') as HTMLInputElement;
      expect(input).toBeTruthy();

      const sendBtn = document.querySelector('.mcs-send-btn');
      expect(sendBtn).toBeTruthy();
    });

    it('quick chat tab shows suggestions area for non-guest', () => {
      render(<MobileChatSheet {...makeProps({ isGuest: false })} />);

      // Quick chat tab should be default for non-guest
      const quickTab = screen.getByRole('tab', { name: /Quick Chat/i });
      fireEvent.click(quickTab);

      expect(screen.getByTestId('quick-chat-suggestions')).toBeTruthy();
      expect(screen.getByText('Quick Chat Suggestions')).toBeTruthy();
    });
  });

  describe('Case 5: Send message', () => {
    it('typing text makes send button active', () => {
      render(<MobileChatSheet {...makeProps()} />);

      // Switch to keyboard tab
      const keyboardTab = screen.getByRole('tab', { name: /keyboard/i });
      fireEvent.click(keyboardTab);

      const input = document.querySelector('.mcs-text-input') as HTMLInputElement;
      const sendBtn = document.querySelector('.mcs-send-btn') as HTMLButtonElement;

      // Initially send should not be active
      expect(sendBtn.classList.contains('mcs-send-active')).toBe(false);

      // Type a message
      fireEvent.change(input, { target: { value: 'Hello world' } });

      // Now send should be active
      const updatedBtn = document.querySelector('.mcs-send-btn') as HTMLButtonElement;
      expect(updatedBtn.classList.contains('mcs-send-active')).toBe(true);
    });

    it('clicking send calls onSendMessage and clears input', () => {
      const onSendMessage = vi.fn();
      render(<MobileChatSheet {...makeProps({ onSendMessage })} />);

      // Switch to keyboard tab
      const keyboardTab = screen.getByRole('tab', { name: /keyboard/i });
      fireEvent.click(keyboardTab);

      const input = document.querySelector('.mcs-text-input') as HTMLInputElement;

      // Type a message
      fireEvent.change(input, { target: { value: 'Hello world' } });

      // Submit the form
      const sendBtn = document.querySelector('.mcs-send-btn') as HTMLButtonElement;
      fireEvent.click(sendBtn);

      expect(onSendMessage).toHaveBeenCalledWith('Hello world');

      // Input should be cleared after send
      expect(input.value).toBe('');
    });

    it('pressing Enter sends the message', () => {
      const onSendMessage = vi.fn();
      render(<MobileChatSheet {...makeProps({ onSendMessage })} />);

      // Switch to keyboard tab
      const keyboardTab = screen.getByRole('tab', { name: /keyboard/i });
      fireEvent.click(keyboardTab);

      const input = document.querySelector('.mcs-text-input') as HTMLInputElement;
      fireEvent.change(input, { target: { value: 'Enter test' } });
      fireEvent.keyDown(input, { key: 'Enter', shiftKey: false });

      expect(onSendMessage).toHaveBeenCalledWith('Enter test');
    });
  });

  describe('Close behavior', () => {
    it('returns null when isOpen is false', () => {
      const { container } = render(<MobileChatSheet {...makeProps({ isOpen: false })} />);
      expect(container.querySelector('.mcs-overlay')).toBeNull();
    });

    it('close button triggers onClose', () => {
      vi.useFakeTimers();
      const onClose = vi.fn();
      render(<MobileChatSheet {...makeProps({ onClose })} />);

      const closeBtn = document.querySelector('.mcs-close-btn') as HTMLButtonElement;
      fireEvent.click(closeBtn);

      // onClose is called after animation timeout (250ms)
      vi.advanceTimersByTime(300);
      expect(onClose).toHaveBeenCalled();
      vi.useRealTimers();
    });
  });

  describe('Table messages', () => {
    it('renders table messages without sender name', () => {
      const messages: ChatMessage[] = [
        makeMessage({ id: 'msg-1', sender: 'Table', message: 'Batman raised to $100', type: 'table' }),
      ];
      const { container } = render(<MobileChatSheet {...makeProps({ messages })} />);

      // Table messages should not have sender element
      const msgEl = container.querySelector('.mcs-msg-table');
      expect(msgEl).toBeTruthy();
      expect(msgEl!.querySelector('.mcs-msg-sender')).toBeNull();
      expect(screen.getByText('Batman raised to $100')).toBeTruthy();
    });

    it('renders GAME START as separator', () => {
      const messages: ChatMessage[] = [
        makeMessage({ id: 'msg-1', sender: 'Table', message: '--- GAME START ---', type: 'table' }),
      ];
      const { container } = render(<MobileChatSheet {...makeProps({ messages })} />);

      const separator = container.querySelector('.mcs-hand-separator');
      expect(separator).toBeTruthy();
      expect(screen.getByText('Game Start')).toBeTruthy();
    });

    it('renders NEW HAND DEALT as separator', () => {
      const messages: ChatMessage[] = [
        makeMessage({ id: 'msg-1', sender: 'Table', message: '--- NEW HAND DEALT ---', type: 'table' }),
      ];
      const { container } = render(<MobileChatSheet {...makeProps({ messages })} />);

      const separator = container.querySelector('.mcs-hand-separator');
      expect(separator).toBeTruthy();
      expect(screen.getByText('New Hand')).toBeTruthy();
    });
  });
});
