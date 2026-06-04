import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, act } from '@testing-library/react';
import { FloatingChat } from '../../components/mobile/FloatingChat';
import type { ChatMessage } from '../../types';

// Mock framer-motion to avoid animation complexities in tests
vi.mock('framer-motion', () => ({
  motion: {
    div: ({ children, ...props }: React.PropsWithChildren<Record<string, unknown>>) => {
      const {
        initial: _initial,
        animate: _animate,
        exit: _exit,
        transition: _transition,
        layout: _layout,
        // Drag-related props are stripped so the resulting <div> is
        // valid HTML. Swipe-to-dismiss isn't exercised here — that
        // path belongs in an E2E test with a real motion runtime.
        drag: _drag,
        dragMomentum: _dragMomentum,
        onDragEnd: _onDragEnd,
        ...htmlProps
      } = props;
      // `style` may contain MotionValues — drop non-serialisable
      // entries so React can apply the rest.
      const safeStyle =
        typeof htmlProps.style === 'object' && htmlProps.style !== null
          ? Object.fromEntries(
              Object.entries(htmlProps.style as Record<string, unknown>).filter(
                ([, v]) => typeof v !== 'object' || v === null
              )
            )
          : htmlProps.style;
      return (
        <div {...htmlProps} style={safeStyle as React.CSSProperties}>
          {children}
        </div>
      );
    },
    // SVG stub for the countdown ring's progress arc. Strips the
    // animation props (pathLength/initial/animate/transition) so the
    // resulting <circle> is valid SVG; the ring's drain animation
    // belongs in an E2E test with a real motion runtime.
    circle: ({ children, ...props }: React.PropsWithChildren<Record<string, unknown>>) => {
      const {
        initial: _initial,
        animate: _animate,
        transition: _transition,
        pathLength: _pathLength,
        ...svgProps
      } = props;
      return <circle {...svgProps}>{children}</circle>;
    },
  },
  AnimatePresence: ({ children }: React.PropsWithChildren<Record<string, unknown>>) => (
    <>{children}</>
  ),
  // Minimal stubs for the swipe-tracking hooks. Tests don't exercise
  // the drag flow itself — the stubs just need to satisfy React's
  // hook-call contract and return MotionValue-shaped objects.
  useMotionValue: (initial: number) => ({ get: () => initial, set: () => {}, on: () => () => {} }),
  useTransform: () => ({ get: () => 1, set: () => {}, on: () => () => {} }),
  animate: () => ({ stop: () => {} }),
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
  return new Map([['Batman', 'http://localhost:5174/avatars/batman.png']]);
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
        />
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
        />
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
        />
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
        />
      );

      const senderEl = document.querySelector('.floating-chat-sender');
      expect(senderEl).toBeTruthy();
      expect(senderEl!.textContent).toBe('raised to $100');
    });
  });

  describe('Dismiss behavior', () => {
    // The X dismiss button was removed in favour of swipe-to-dismiss
    // and the existing auto-dismiss timer. Swipe gestures aren't
    // exercised here — the framer-motion mock strips drag handlers,
    // so that path belongs in an end-to-end test with a real motion
    // runtime. The TTL-based dismiss is covered by the
    // "Auto-dismiss via TTL" suite below.
    it('does not render an X dismiss button', () => {
      const onDismiss = vi.fn();
      render(
        <FloatingChat
          message={makeMessage()}
          onDismiss={onDismiss}
          playerAvatars={makePlayerAvatars()}
        />
      );

      expect(document.querySelector('.floating-chat')).toBeTruthy();
      expect(document.querySelector('.floating-chat-dismiss')).toBeNull();
    });
  });

  describe('Null message renders nothing', () => {
    it('renders nothing when message is null', () => {
      const onDismiss = vi.fn();
      const { container } = render(
        <FloatingChat message={null} onDismiss={onDismiss} playerAvatars={makePlayerAvatars()} />
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
        />
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
        />
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
        />
      );

      const aiBadge = document.querySelector('.ai-badge');
      expect(aiBadge).toBeTruthy();
      expect(aiBadge!.textContent).toBe('AI');
    });

    it('falls back to the message avatar_url when the sender has left the table', () => {
      // A departed AI's farewell lands after the seat-derived cache drops them,
      // so the cache (playerAvatars) no longer has the sender. The avatar URL
      // stamped on the message keeps the bubble's face from going blank.
      const onDismiss = vi.fn();
      render(
        <FloatingChat
          message={makeMessage({
            sender: 'Batman',
            type: 'ai',
            avatar_url: 'http://localhost:5174/avatars/batman.png',
          })}
          onDismiss={onDismiss}
          playerAvatars={new Map()}
        />
      );

      const avatarImg = document.querySelector('.floating-avatar-img') as HTMLImageElement;
      expect(avatarImg).toBeTruthy();
      expect(avatarImg.src).toContain('/avatars/batman.png');
    });

    it('prefers the live cache avatar over the message avatar_url for seated players', () => {
      const onDismiss = vi.fn();
      render(
        <FloatingChat
          message={makeMessage({
            sender: 'Batman',
            type: 'ai',
            avatar_url: 'http://localhost:5174/avatars/stale.png',
          })}
          onDismiss={onDismiss}
          playerAvatars={makePlayerAvatars()}
        />
      );

      const avatarImg = document.querySelector('.floating-avatar-img') as HTMLImageElement;
      expect(avatarImg).toBeTruthy();
      // Live, emotion-aware cache wins while the player is still seated.
      expect(avatarImg.src).toContain('/avatars/batman.png');
    });
  });

  describe('Message stacking', () => {
    it('stacks multiple messages when new ones arrive', () => {
      const onDismiss = vi.fn();
      const msg1 = makeMessage({ id: 'msg-1', sender: 'Batman', message: 'First' });
      const msg2 = makeMessage({ id: 'msg-2', sender: 'Gandalf', message: 'Second' });

      const { rerender } = render(
        <FloatingChat message={msg1} onDismiss={onDismiss} playerAvatars={makePlayerAvatars()} />
      );

      // First message should be rendered
      expect(document.querySelectorAll('.floating-chat').length).toBe(1);

      // Rerender with a new message
      rerender(
        <FloatingChat message={msg2} onDismiss={onDismiss} playerAvatars={makePlayerAvatars()} />
      );

      // Both messages should be stacked
      expect(document.querySelectorAll('.floating-chat').length).toBe(2);
    });

    it('does not add the same message twice (deduplicates by id)', () => {
      const onDismiss = vi.fn();
      const msg = makeMessage({ id: 'msg-dup' });

      const { rerender } = render(
        <FloatingChat message={msg} onDismiss={onDismiss} playerAvatars={makePlayerAvatars()} />
      );

      expect(document.querySelectorAll('.floating-chat').length).toBe(1);

      // Rerender with same message again
      rerender(
        <FloatingChat message={msg} onDismiss={onDismiss} playerAvatars={makePlayerAvatars()} />
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
        <FloatingChat message={msg} onDismiss={onDismiss} playerAvatars={makePlayerAvatars()} />
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
