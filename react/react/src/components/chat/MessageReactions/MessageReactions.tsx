import { memo, useMemo } from 'react';
import { ThumbsUp, ThumbsDown } from 'lucide-react';
import type { MessageReactions, ReactionSentiment } from '../../../types/chat';
import './MessageReactions.css';

interface ReactionButtonsProps {
  messageId: string;
  reactions: MessageReactions | undefined;
  playerName: string;
  onReact: (messageId: string, sentiment: ReactionSentiment | null) => void;
  /** Render the floating-bubble variant (slightly smaller, lighter chrome). */
  variant?: 'default' | 'floating';
}

interface ReactionChipsProps {
  reactions: MessageReactions | undefined;
}

interface AggregateEntry {
  emoji: string;
  count: number;
  reactorList: string[];
}

// Group reactor entries by the rolled emoji. Insertion order in the
// reactions dict mirrors the order players clicked, which is good
// enough for chip ordering — newest reactor's emoji shows last.
function aggregate(reactions: MessageReactions | undefined): AggregateEntry[] {
  if (!reactions) return [];
  const byEmoji = new Map<string, AggregateEntry>();
  for (const [reactor, record] of Object.entries(reactions)) {
    const existing = byEmoji.get(record.emoji);
    byEmoji.set(
      record.emoji,
      existing
        ? { ...existing, count: existing.count + 1, reactorList: [...existing.reactorList, reactor] }
        : { emoji: record.emoji, count: 1, reactorList: [reactor] },
    );
  }
  return Array.from(byEmoji.values());
}

/**
 * Right-edge vertical button stack: positive on top, negative below.
 * Absolutely positioned by the parent's `position: relative` context
 * so the message box height does not change. The two parent
 * containers (chat list rows + floating bubble) opt in via the
 * `has-reactions` class on their message wrapper.
 */
export const ReactionButtons = memo(function ReactionButtons({
  messageId,
  reactions,
  playerName,
  onReact,
  variant = 'default',
}: ReactionButtonsProps) {
  const myReaction = reactions?.[playerName];
  const iconSize = variant === 'floating' ? 12 : 13;

  return (
    <div className={`reaction-btn-stack reaction-btn-stack--${variant}`}>
      <button
        type="button"
        className={`reaction-btn positive ${myReaction?.sentiment === 'positive' ? 'active' : ''}`}
        onClick={(e) => {
          e.stopPropagation();
          onReact(messageId, 'positive');
        }}
        aria-pressed={myReaction?.sentiment === 'positive'}
        aria-label="React positively"
        title="React positively"
      >
        <ThumbsUp size={iconSize} strokeWidth={2.2} aria-hidden="true" />
      </button>
      <button
        type="button"
        className={`reaction-btn negative ${myReaction?.sentiment === 'negative' ? 'active' : ''}`}
        onClick={(e) => {
          e.stopPropagation();
          onReact(messageId, 'negative');
        }}
        aria-pressed={myReaction?.sentiment === 'negative'}
        aria-label="React negatively"
        title="React negatively"
      >
        <ThumbsDown size={iconSize} strokeWidth={2.2} aria-hidden="true" />
      </button>
    </div>
  );
});

/**
 * Inline emoji-count chips, intended to render at the tail of the
 * message text so reactions don't push the message taller. Empty
 * when no reactions exist (renders nothing rather than a hollow
 * container so flex parents don't gap-around it).
 */
export const ReactionChips = memo(function ReactionChips({ reactions }: ReactionChipsProps) {
  const aggregates = useMemo(() => aggregate(reactions), [reactions]);
  if (aggregates.length === 0) return null;
  return (
    <span className="reaction-chips" data-testid="reaction-counts">
      {aggregates.map(({ emoji, count, reactorList }) => (
        <span
          key={emoji}
          className="reaction-chip"
          title={reactorList.join(', ')}
        >
          <span aria-hidden="true">{emoji}</span>
          {count > 1 && <span className="reaction-count">{count}</span>}
        </span>
      ))}
    </span>
  );
});
