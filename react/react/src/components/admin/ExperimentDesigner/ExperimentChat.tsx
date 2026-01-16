import { useState, useEffect, useRef, useCallback } from 'react';
import { Send, Loader2, Sparkles } from 'lucide-react';
import type { ExperimentConfig } from './types';
import { config as appConfig } from '../../../config';

interface QuickPrompt {
  id: string;
  label: string;
  prompt: string;
}

interface ChatMessage {
  role: 'user' | 'assistant';
  content: string;
}

interface ExperimentChatProps {
  config: ExperimentConfig;
  sessionId: string | null;
  onSessionIdChange: (sessionId: string) => void;
  onConfigUpdate: (updates: Partial<ExperimentConfig>) => void;
}

export function ExperimentChat({
  config,
  sessionId,
  onSessionIdChange,
  onConfigUpdate,
}: ExperimentChatProps) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [quickPrompts, setQuickPrompts] = useState<QuickPrompt[]>([]);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  // Fetch quick prompts on mount
  useEffect(() => {
    const fetchQuickPrompts = async () => {
      try {
        const response = await fetch(`${appConfig.API_URL}/api/experiments/quick-prompts`);
        const data = await response.json();
        if (data.success) {
          setQuickPrompts(data.prompts);
        }
      } catch (err) {
        console.error('Failed to load quick prompts:', err);
      }
    };
    fetchQuickPrompts();
  }, []);

  // Auto-scroll to bottom when messages change
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  // Add initial welcome message
  useEffect(() => {
    if (messages.length === 0) {
      setMessages([
        {
          role: 'assistant',
          content: "Hi! I'm your experiment design assistant. Tell me what you want to test, and I'll help you configure an AI poker tournament experiment.\n\nYou can describe your testing goals, or use one of the quick prompts below to get started.",
        },
      ]);
    }
  }, []);

  const sendMessage = useCallback(async (messageText: string) => {
    if (!messageText.trim() || loading) return;

    // Add user message to display
    const userMessage: ChatMessage = { role: 'user', content: messageText };
    setMessages(prev => [...prev, userMessage]);
    setInput('');
    setLoading(true);

    try {
      const response = await fetch(`${appConfig.API_URL}/api/experiments/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          message: messageText,
          session_id: sessionId,
          current_config: config,
        }),
      });

      const data = await response.json();

      if (data.success) {
        // Update session ID if new
        if (data.session_id && data.session_id !== sessionId) {
          onSessionIdChange(data.session_id);
        }

        // Add assistant response
        const assistantMessage: ChatMessage = {
          role: 'assistant',
          content: data.response,
        };
        setMessages(prev => [...prev, assistantMessage]);

        // Apply config updates if present
        if (data.config_updates) {
          onConfigUpdate(data.config_updates);
        }
      } else {
        // Add error message
        setMessages(prev => [
          ...prev,
          {
            role: 'assistant',
            content: `Error: ${data.error || 'Failed to get response'}`,
          },
        ]);
      }
    } catch (err) {
      setMessages(prev => [
        ...prev,
        {
          role: 'assistant',
          content: 'Error: Failed to connect to server. Please try again.',
        },
      ]);
    } finally {
      setLoading(false);
    }
  }, [config, loading, onConfigUpdate, onSessionIdChange, sessionId]);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    sendMessage(input);
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendMessage(input);
    }
  };

  const handleQuickPrompt = (prompt: QuickPrompt) => {
    sendMessage(prompt.prompt);
  };

  return (
    <div className="experiment-chat">
      <div className="experiment-chat__messages">
        {messages.map((message, index) => (
          <div
            key={index}
            className={`experiment-chat__message experiment-chat__message--${message.role}`}
          >
            <div className="experiment-chat__message-content">
              {message.content.split('\n').map((line, i) => (
                <p key={i}>{line || '\u00A0'}</p>
              ))}
            </div>
          </div>
        ))}

        {loading && (
          <div className="experiment-chat__message experiment-chat__message--assistant">
            <div className="experiment-chat__typing">
              <Loader2 size={16} className="animate-spin" />
              <span>Thinking...</span>
            </div>
          </div>
        )}

        <div ref={messagesEndRef} />
      </div>

      {/* Quick Prompts */}
      {messages.length <= 1 && quickPrompts.length > 0 && (
        <div className="experiment-chat__quick-prompts">
          <div className="experiment-chat__quick-prompts-label">
            <Sparkles size={14} />
            Quick Start
          </div>
          <div className="experiment-chat__quick-prompts-list">
            {quickPrompts.map((prompt) => (
              <button
                key={prompt.id}
                className="experiment-chat__quick-prompt-btn"
                onClick={() => handleQuickPrompt(prompt)}
                type="button"
                disabled={loading}
              >
                {prompt.label}
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Input Area */}
      <form className="experiment-chat__input-area" onSubmit={handleSubmit}>
        <textarea
          ref={inputRef}
          className="experiment-chat__input"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Describe what you want to test..."
          disabled={loading}
          rows={2}
        />
        <button
          className="experiment-chat__send-btn"
          type="submit"
          disabled={!input.trim() || loading}
        >
          {loading ? <Loader2 size={18} className="animate-spin" /> : <Send size={18} />}
        </button>
      </form>
    </div>
  );
}

export default ExperimentChat;
