import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import './ChatMarkdown.css';

interface ChatMarkdownProps {
  children: string;
  className?: string;
}

/**
 * Shared renderer for LLM/chat markdown output (bold, lists, tables, code).
 *
 * Uses react-markdown with GFM. Raw HTML is NOT enabled (no rehype-raw), so
 * model output can't inject markup — safe to render assistant text directly.
 * Scope styling via the `.chat-markdown` class (see ChatMarkdown.css).
 */
export function ChatMarkdown({ children, className }: ChatMarkdownProps) {
  return (
    <div className={`chat-markdown ${className ?? ''}`}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          // Open any links the model emits safely in a new tab.
          a: ({ ...props }) => <a target="_blank" rel="noreferrer noopener" {...props} />,
        }}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
}
