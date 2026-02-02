import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, fireEvent, act } from '@testing-library/react';
import { FloatingChat } from '../../components/mobile/FloatingChat';
import type { ChatMessage } from '../../types';

// Mock framer-motion to avoid animation complexities in tests
vi.mock('framer-motion', () => ({
  motion: {
    div: ({ children, ...props }: React.PropsWithChildren<Record<string, unknown>>) => {
      const { initial: _initial, animate: _animate, exit: _exit, transition: _transition, layout: _layout, ...htmlProps } = props;
      return <div {...htmlProps}>{children}</div>;
    },
  },
  AnimatePresence: ({ children }: React.PropsWithChildren<Record<string, unknown>>) => <>{children}</>,
}));

function makeMessage(overrides: Partial<ChatMessage> = {}): ChatMessage {
  return {
    id: 'msg-1',
    sender: 'Batman',
    message: 'I am the night.',
    timestamp: new Date().toISOString(),
    type: 'ai',
    ...overrides,
  };
}

function makePlayerAvatars(): Map<string, string> {
  return new Map([
    ['Batman', 'http://localhost:5174/avatars/batman.png'],
  ]);
}

describe('VT-05: FloatingChat — message stacking, timing, dismiss', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  describe('Message rendering', () => {
    it('renders a bubble when a message is passed', () => {
      const onDismiss = vi.fn();
      render(
        <FloatingChat
          message={makeMessage()}
          onDismiss={onDismiss}
          playerAvatars={makePlayerAvatars()}
        />,
      );

      // The floating-chat-stack should be present
      expect(document.querySelector('.floating-chat-stack')).toBeTruthy();
      // A floating-chat bubble should exist
      expect(document.querySelector('.floating-chat')).toBeTruthy();
    });

    it('shows sender name in the bubble', () => {
      const onDismiss = vi.fn();
      render(
        <FloatingChat
          message={makeMessage()}
          onDismiss={onDismiss}
          playerAvatars={makePlayerAvatars()}
        />,
      );

      // Sender shown in .floating-chat-sender (shows action or sender)
      const senderEl = document.querySelector('.floating-chat-sender');
      expect(senderEl).toBeTruthy();
      expect(senderEl!.textContent).toBe('Batman');
    });

    it('shows message text (speech beat types out over time)', () => {
      const onDismiss = vi.fn();
      render(
        <FloatingChat
          message={makeMessage({ message: 'Hello' })}
          onDismiss={onDismiss}
          playerAvatars={makePlayerAvatars()}
        />,
      );

      // The message container should exist
      const messageEl = document.querySelector('.floating-chat-message');
      expect(messageEl).toBeTruthy();

      // Advance timers tick by tick to let setInterval fire for each character
      // "Hello" = 5 chars, typing speed = 30ms each
      // Need to advance enough for all chars plus the initial SpeechBeat start delay (0ms for first beat)
      for (let i = 0; i < 10; i++) {
        act(() => {
          vi.advanceTimersByTime(30);
        });
      }

      expect(messageEl!.textContent).toContain('Hello');
    });

    it('shows action text as sender when action is present', () => {
      const onDismiss = vi.fn();
      render(
        <FloatingChat
          message={makeMessage({ action: 'raised to $100', message: '' })}
          onDismiss={onDismiss}
          playerAvatars={makePlayerAvatars()}
        />,
      );

      const senderEl = document.querySelector('.floating-chat-sender');
      expect(senderEl).toBeTruthy();
      expect(senderEl!.textContent).toBe('raised to $100');
    });
  });

  describe('Dismiss behavior', () => {
    it('dismiss button removes the message', () => {
      const onDismiss = vi.fn();
      render(
        <FloatingChat
          message={makeMessage()}
          onDismiss={onDismiss}
          playerAvatars={makePlayerAvatars()}
        />,
      );

      // Bubble should exist
      expect(document.querySelector('.floating-chat')).toBeTruthy();

      // Click dismiss button
      const dismissBtn = document.querySelector('.floating-chat-dismiss');
      expect(dismissBtn).toBeTruthy();
      fireEvent.click(dismissBtn!);

      // After dismissing the only message, stack should be gone (returns null)
      expect(document.querySelector('.floating-chat')).toBeNull();
    });
  });

  describe('Null message renders nothing', () => {
    it('renders nothing when message is null', () => {
      const onDismiss = vi.fn();
      const { container } = render(
        <FloatingChat
          message={null}
          onDismiss={onDismiss}
          playerAvatars={makePlayerAvatars()}
        />,
      );

      expect(container.querySelector('.floating-chat-stack')).toBeNull();
      expect(container.querySelector('.floating-chat')).toBeNull();
    });
  });

  describe('Avatar display', () => {
    it('shows avatar image for AI player with avatar_url', () => {
      const onDismiss = vi.fn();
      render(
        <FloatingChat
          message={makeMessage()}
          onDismiss={onDismiss}
          playerAvatars={makePlayerAvatars()}
        />,
      );

      const avatarImg = document.querySelector('.floating-avatar-img') as HTMLImageElement;
      expect(avatarImg).toBeTruthy();
      expect(avatarImg.src).toContain('/avatars/batman.png');
    });

    it('shows initial letter when no avatar_url', () => {
      const onDismiss = vi.fn();

      render(
        <FloatingChat
          message={makeMessage({ sender: 'Gandalf' })}
          onDismiss={onDismiss}
          playerAvatars={new Map()}
        />,
      );

      const avatarEl = document.querySelector('.floating-chat-avatar');
      expect(avatarEl).toBeTruthy();
      // Should show "G" initial since no avatar_url
      expect(avatarEl!.textContent).toContain('G');
    });

    it('shows AI badge for AI messages without avatar', () => {
      const onDismiss = vi.fn();

      render(
        <FloatingChat
          message={makeMessage({ sender: 'Gandalf', type: 'ai' })}
          onDismiss={onDismiss}
          playerAvatars={new Map()}
        />,
      );

      const aiBadge = document.querySelector('.ai-badge');
      expect(aiBadge).toBeTruthy();
      expect(aiBadge!.textContent).toBe('AI');
    });
  });

  describe('Message stacking', () => {
    it('stacks multiple messages when new ones arrive', () => {
      const onDismiss = vi.fn();
      const msg1 = makeMessage({ id: 'msg-1', sender: 'Batman', message: 'First' });
      const msg2 = makeMessage({ id: 'msg-2', sender: 'Gandalf', message: 'Second' });

      const { rerender } = render(
        <FloatingChat message={msg1} onDismiss={onDismiss} playerAvatars={makePlayerAvatars()} />,
      );

      // First message should be rendered
      expect(document.querySelectorAll('.floating-chat').length).toBe(1);

      // Rerender with a new message
      rerender(
        <FloatingChat message={msg2} onDismiss={onDismiss} playerAvatars={makePlayerAvatars()} />,
      );

      // Both messages should be stacked
      expect(document.querySelectorAll('.floating-chat').length).toBe(2);
    });

    it('does not add the same message twice (deduplicates by id)', () => {
      const onDismiss = vi.fn();
      const msg = makeMessage({ id: 'msg-dup' });

      const { rerender } = render(
        <FloatingChat message={msg} onDismiss={onDismiss} playerAvatars={makePlayerAvatars()} />,
      );

      expect(document.querySelectorAll('.floating-chat').length).toBe(1);

      // Rerender with same message again
      rerender(
        <FloatingChat message={msg} onDismiss={onDismiss} playerAvatars={makePlayerAvatars()} />,
      );

      // Still only one message
      expect(document.querySelectorAll('.floating-chat').length).toBe(1);
    });
  });

  describe('Auto-dismiss via TTL', () => {
    it('messages auto-dismiss after their calculated display duration', () => {
      const onDismiss = vi.fn();
      // Short message: "Hi" = 2 chars
      // Duration: max(3000, 2*(30+20) + 2000) = max(3000, 2100) = 3000ms (minimum)
      const msg = makeMessage({ id: 'msg-ttl', message: 'Hi' });

      render(
        <FloatingChat message={msg} onDismiss={onDismiss} playerAvatars={makePlayerAvatars()} />,
      );

      expect(document.querySelector('.floating-chat')).toBeTruthy();

      // Advance past minimum duration (3000ms)
      act(() => {
        vi.advanceTimersByTime(3500);
      });

      // Message should be gone and onDismiss called (called when all messages clear)
      expect(document.querySelector('.floating-chat')).toBeNull();
      expect(onDismiss).toHaveBeenCalled();
    });
  });
});
