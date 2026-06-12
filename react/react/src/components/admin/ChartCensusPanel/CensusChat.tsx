import { useEffect, useRef, useState } from 'react';
import { Send, Sparkles, AlertTriangle } from 'lucide-react';
import { adminFetch } from '../../../utils/api';
import { ChatMarkdown } from '../../shared/ChatMarkdown';

interface Msg {
  role: 'user' | 'assistant';
  content: string;
}

const SUGGESTIONS = [
  'Which chart gap should I fix first, and why?',
  'Which gaps matter most against maniacs?',
  'Where is the most bb at risk?',
  'Summarize the top 3 opportunities.',
];

export function CensusChat() {
  const [messages, setMessages] = useState<Msg[]>([]);
  const [input, setInput] = useState('');
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const listRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    listRef.current?.scrollTo({ top: listRef.current.scrollHeight, behavior: 'smooth' });
  }, [messages, sending]);

  async function ask(question: string) {
    const q = question.trim();
    if (!q || sending) return;
    const next: Msg[] = [...messages, { role: 'user', content: q }];
    setMessages(next);
    setInput('');
    setError(null);
    setSending(true);
    try {
      const resp = await adminFetch('/api/admin/chart-census/ask', {
        method: 'POST',
        body: JSON.stringify({ messages: next }),
      });
      const data = await resp.json();
      if (!resp.ok) throw new Error(data?.message || `HTTP ${resp.status}`);
      setMessages([...next, { role: 'assistant', content: data.answer }]);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSending(false);
    }
  }

  return (
    <section className="cc-chat">
      <div className="cc-chat-head">
        <Sparkles size={16} />
        <span>Ask the analyst</span>
        <span className="cc-chat-sub">grounded on this census — won't invent numbers</span>
      </div>

      <div className="cc-chat-list" ref={listRef}>
        {messages.length === 0 && (
          <div className="cc-chat-suggest">
            {SUGGESTIONS.map((s) => (
              <button
                key={s}
                className="cc-chat-chip"
                onClick={() => void ask(s)}
                disabled={sending}
              >
                {s}
              </button>
            ))}
          </div>
        )}
        {messages.map((m, i) => (
          <div key={i} className={`cc-msg ${m.role}`}>
            <div className="cc-msg-body">
              {m.role === 'assistant' ? <ChatMarkdown>{m.content}</ChatMarkdown> : m.content}
            </div>
          </div>
        ))}
        {sending && (
          <div className="cc-msg assistant">
            <div className="cc-msg-body cc-typing">Thinking…</div>
          </div>
        )}
      </div>

      {error && (
        <div className="cc-chat-error">
          <AlertTriangle size={14} /> {error}
        </div>
      )}

      <form
        className="cc-chat-input"
        onSubmit={(e) => {
          e.preventDefault();
          void ask(input);
        }}
      >
        <input
          type="text"
          value={input}
          placeholder="Ask about the census…"
          onChange={(e) => setInput(e.target.value)}
          disabled={sending}
        />
        <button type="submit" disabled={sending || !input.trim()} aria-label="Send">
          <Send size={16} />
        </button>
      </form>
    </section>
  );
}
