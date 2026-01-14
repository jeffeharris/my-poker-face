import { useState, useRef, useEffect } from 'react';
import type { InterrogationMessage, PromptCapture, InterrogationResponse, ProviderInfo } from './types';
import { config } from '../../../config';

interface InterrogationChatProps {
  capture: PromptCapture;
  messages: InterrogationMessage[];
  onMessagesUpdate: (messages: InterrogationMessage[]) => void;
  sessionId: string | null;
  onSessionIdUpdate: (sessionId: string | null) => void;
  provider: string;
  onProviderChange: (provider: string) => void;
  model: string;
  onModelChange: (model: string) => void;
  reasoningEffort: string;
  onReasoningEffortChange: (effort: string) => void;
  providers: ProviderInfo[];
  getModelsForProvider: (provider: string) => string[];
  reasoningLevels: string[];
}

// Suggested quick questions based on action type
const QUICK_QUESTIONS: Record<string, string[]> = {
  fold: [
    "Why did you fold instead of calling?",
    "What would you have needed to stay in?",
    "Were you bluffing on previous rounds?",
  ],
  call: [
    "Why call instead of raising?",
    "What hands were you putting your opponent on?",
    "How did pot odds influence your decision?",
  ],
  raise: [
    "Why did you raise that specific amount?",
    "Were you trying to build the pot or push players out?",
    "What was your read on the table?",
  ],
  check: [
    "Why check instead of betting?",
    "Were you trapping or genuinely weak?",
    "What would have made you bet?",
  ],
  default: [
    "Walk me through your reasoning.",
    "What factors influenced your decision most?",
    "What were you thinking about the other players?",
  ],
};

export function InterrogationChat({
  capture,
  messages,
  onMessagesUpdate,
  sessionId,
  onSessionIdUpdate,
  provider,
  onProviderChange,
  model,
  onModelChange,
  reasoningEffort,
  onReasoningEffortChange,
  providers,
  getModelsForProvider,
  reasoningLevels,
}: InterrogationChatProps) {
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages]);

  const handleSend = async () => {
    if (!input.trim() || loading) return;

    const userMessage: InterrogationMessage = {
      id: `user-${Date.now()}`,
      role: 'user',
      content: input.trim(),
      timestamp: new Date().toISOString(),
    };

    onMessagesUpdate([...messages, userMessage]);
    setInput('');
    setLoading(true);
    setError(null);

    try {
      const response = await fetch(
        `${config.API_URL}/api/prompt-debug/captures/${capture.id}/interrogate`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          credentials: 'include',
          body: JSON.stringify({
            message: userMessage.content,
            session_id: sessionId,
            provider: provider,
            model: model,
            reasoning_effort: reasoningEffort,
          }),
        }
      );

      if (!response.ok) {
        throw new Error('Failed to get response');
      }

      const data: InterrogationResponse = await response.json();

      if (!data.success) {
        throw new Error(data.error || 'Unknown error');
      }

      // Update session ID
      onSessionIdUpdate(data.session_id);

      // Add AI response
      const aiMessage: InterrogationMessage = {
        id: `ai-${Date.now()}`,
        role: 'assistant',
        content: data.response,
        timestamp: new Date().toISOString(),
      };

      onMessagesUpdate([...messages, userMessage, aiMessage]);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unknown error');
    } finally {
      setLoading(false);
    }
  };

  const handleQuickQuestion = (question: string) => {
    setInput(question);
  };

  const handleReset = () => {
    onMessagesUpdate([{
      id: 'original-decision',
      role: 'context',
      content: capture.ai_response,
      timestamp: capture.created_at,
    }]);
    onSessionIdUpdate(null);
    setError(null);
  };

  const quickQuestions = QUICK_QUESTIONS[capture.action_taken || 'default'] || QUICK_QUESTIONS.default;

  return (
    <div className="interrogation-chat">
      {/* Context Summary */}
      <details className="interrogation-context">
        <summary>Original Decision Context</summary>
        <div className="context-preview">
          <div className="context-row">
            <span className="label">Action:</span>
            <span className="value">{capture.action_taken?.toUpperCase()}</span>
          </div>
          <div className="context-row">
            <span className="label">Phase:</span>
            <span className="value">{capture.phase}</span>
          </div>
          <div className="context-row">
            <span className="label">Pot Odds:</span>
            <span className="value">{capture.pot_odds?.toFixed(1)}:1</span>
          </div>
          <div className="context-row">
            <span className="label">Hand:</span>
            <span className="value">{capture.player_hand?.join(' ') || '-'}</span>
          </div>
        </div>
      </details>

      {/* Messages */}
      <div className="interrogation-messages">
        {messages.map((msg) => (
          <div
            key={msg.id}
            className={`interrogation-message ${msg.role}`}
          >
            <div className="message-header">
              <span className="message-role">
                {msg.role === 'context' ? 'Original Decision' :
                 msg.role === 'user' ? 'You' : capture.player_name}
              </span>
            </div>
            <div className="message-content">{msg.content}</div>
          </div>
        ))}
        {loading && (
          <div className="interrogation-message assistant loading">
            <div className="typing-indicator">
              <span></span><span></span><span></span>
            </div>
          </div>
        )}
        <div ref={messagesEndRef} />
      </div>

      {error && <div className="interrogation-error">{error}</div>}

      {/* Quick Questions */}
      {messages.length <= 1 && (
        <div className="quick-questions">
          <span className="quick-label">Quick questions:</span>
          {quickQuestions.map((q, i) => (
            <button
              key={i}
              className="quick-question-btn"
              onClick={() => handleQuickQuestion(q)}
            >
              {q}
            </button>
          ))}
        </div>
      )}

      {/* Provider, Model, and Reasoning Settings */}
      <div className="interrogation-settings">
        <div className="setting-group">
          <label>Provider:</label>
          <select
            value={provider}
            onChange={(e) => onProviderChange(e.target.value)}
            disabled={sessionId !== null}
            title={sessionId ? "Reset to change provider" : "Select provider"}
          >
            {providers.length > 0 ? (
              providers.map(p => (
                <option key={p.name} value={p.name}>{p.name}</option>
              ))
            ) : (
              <option value="openai">openai</option>
            )}
          </select>
        </div>
        <div className="setting-group">
          <label>Model:</label>
          <select
            value={model}
            onChange={(e) => onModelChange(e.target.value)}
            disabled={sessionId !== null}
            title={sessionId ? "Reset to change model" : "Select model"}
          >
            {getModelsForProvider(provider).map(m => (
              <option key={m} value={m}>{m}</option>
            ))}
          </select>
        </div>
        <div className="setting-group">
          <label>Reasoning:</label>
          <select
            value={reasoningEffort}
            onChange={(e) => onReasoningEffortChange(e.target.value)}
            disabled={sessionId !== null}
            title={sessionId ? "Reset to change reasoning" : "Select reasoning level"}
          >
            {reasoningLevels.map(level => (
              <option key={level} value={level}>{level}</option>
            ))}
          </select>
        </div>
        {sessionId && (
          <span className="settings-note">Reset to change settings</span>
        )}
      </div>

      {/* Input */}
      <form
        className="interrogation-input"
        onSubmit={(e) => {
          e.preventDefault();
          handleSend();
        }}
      >
        <input
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Ask a follow-up question..."
          disabled={loading}
        />
        <button type="submit" disabled={loading || !input.trim()}>
          {loading ? 'Sending...' : 'Send'}
        </button>
        <button
          type="button"
          className="reset-btn"
          onClick={handleReset}
          title="Reset conversation"
        >
          Reset
        </button>
      </form>
    </div>
  );
}
