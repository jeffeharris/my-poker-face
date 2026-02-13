import { useState, useEffect, useRef, useCallback } from 'react';
import { Send, Loader2, Sparkles, GitCompare, Beaker, Repeat2 } from 'lucide-react';
import type { ExperimentConfig, LabAssistantContext, ConfigVersion, ChatMessage, ExperimentType } from './types';
import { adminFetch } from '../../../utils/api';
import { logger } from '../../../utils/logger';

interface QuickPrompt {
  id: string;
  label: string;
  prompt: string;
  type?: 'tournament' | 'replay';
}

export interface InitialMessage {
  userMessage: string;
  context?: LabAssistantContext;
}

export interface ExperimentChatProps {
  config: ExperimentConfig;
  sessionId: string | null;
  onSessionIdChange: (sessionId: string) => void;
  onConfigUpdate: (updates: Partial<ExperimentConfig>) => void;
  /** Initial message to send on mount (e.g., for failure analysis) */
  initialMessage?: InitialMessage | null;
  /** Chat history to restore from a previous session */
  initialChatHistory?: ChatMessage[];
  configVersions?: ConfigVersion[];
  onConfigVersionsChange?: (versions: ConfigVersion[]) => void;
  currentVersionIndex?: number;
  onCurrentVersionIndexChange?: (index: number) => void;
  /** Current experiment type (tournament, replay, or undetermined) */
  experimentType?: ExperimentType | 'undetermined';
  /** Callback when experiment type changes */
  onExperimentTypeChange?: (type: ExperimentType | 'undetermined') => void;
}

export function ExperimentChat({
  config,
  sessionId,
  onSessionIdChange,
  onConfigUpdate,
  initialMessage,
  initialChatHistory,
  configVersions: _configVersions,
  onConfigVersionsChange,
  currentVersionIndex: _currentVersionIndex,
  onCurrentVersionIndexChange,
  experimentType = 'undetermined',
  onExperimentTypeChange,
}: ExperimentChatProps) {
  // Initialize messages from initial history if provided
  const [messages, setMessages] = useState<ChatMessage[]>(initialChatHistory || []);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [quickPrompts, setQuickPrompts] = useState<QuickPrompt[]>([]);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const hasSentInitialMessage = useRef(false);

  // Fetch quick prompts - refetch when experiment type changes
  useEffect(() => {
    const fetchQuickPrompts = async () => {
      try {
        // Pass type filter if determined, otherwise fetch all
        const typeParam = experimentType !== 'undetermined' ? `?type=${experimentType}` : '';
        const response = await adminFetch(`/api/experiments/quick-prompts${typeParam}`);
        const data = await response.json();
        if (data.success) {
          setQuickPrompts(data.prompts);
        }
      } catch (err) {
        logger.error('Failed to load quick prompts:', err);
      }
    };
    fetchQuickPrompts();
  }, [experimentType]);

  // Auto-scroll to bottom when messages change
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  // Define sendMessage BEFORE the useEffect that uses it
  const sendMessage = useCallback(async (messageText: string, contextForFailure?: LabAssistantContext | null, forceExperimentType?: ExperimentType) => {
    if (!messageText.trim() || loading) return;

    // Add user message to display
    const userMessage: ChatMessage = { role: 'user', content: messageText };
    setMessages(prev => [...prev, userMessage]);
    setInput('');
    setLoading(true);

    try {
      const response = await adminFetch('/api/experiments/chat', {
        method: 'POST',
        body: JSON.stringify({
          message: messageText,
          session_id: sessionId,
          current_config: config,
          failure_context: contextForFailure || null,
          experiment_type: forceExperimentType || experimentType,
        }),
      });

      const data = await response.json();

      if (data.success) {
        // Update session ID if new
        if (data.session_id && data.session_id !== sessionId) {
          onSessionIdChange(data.session_id);
        }

        // Update experiment type if returned from backend
        if (data.experiment_type && data.experiment_type !== experimentType) {
          onExperimentTypeChange?.(data.experiment_type);
        }

        // Add assistant response with optional config diff
        const assistantMessage: ChatMessage = {
          role: 'assistant',
          content: data.response,
          configDiff: data.config_diff || undefined,
        };
        setMessages(prev => [...prev, assistantMessage]);

        // Apply config updates if present
        if (data.config_updates) {
          onConfigUpdate(data.config_updates);
        }

        // Update config versions if present
        if (data.config_versions && onConfigVersionsChange) {
          onConfigVersionsChange(data.config_versions);
          if (data.current_version_index !== undefined && onCurrentVersionIndexChange) {
            onCurrentVersionIndexChange(data.current_version_index);
          }
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
    } catch {
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
  }, [config, loading, onConfigUpdate, onSessionIdChange, sessionId, onConfigVersionsChange, onCurrentVersionIndexChange, experimentType, onExperimentTypeChange]);

  // Handle initial message on mount (e.g., for failure analysis)
  // Use ref to prevent double-send in React StrictMode (state checks are unreliable due to async updates)
  useEffect(() => {
    if (initialMessage && !hasSentInitialMessage.current) {
      hasSentInitialMessage.current = true;
      sendMessage(initialMessage.userMessage, initialMessage.context);
    }
  }, [initialMessage, sendMessage]);

  // Add initial welcome message (only on mount when messages is empty and no initial history)
  useEffect(() => {
    if (messages.length === 0 && !initialMessage && !initialChatHistory) {
      const welcomeMessage = experimentType === 'undetermined'
        ? "Hi! I'm your Lab Assistant. What would you like to test?\n\nChoose an experiment type below, or just describe what you want to figure out and I'll help you design the right experiment."
        : experimentType === 'tournament'
        ? "Great! Let's design a tournament experiment. Tell me what you want to test, or use one of the quick prompts below to get started."
        : "Great! Let's design a replay experiment. Tell me what you want to test with captured decisions, or use one of the quick prompts below to get started.";

      setMessages([
        {
          role: 'assistant',
          content: welcomeMessage,
        },
      ]);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- intentionally run only on mount
  }, []);

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
    // If the prompt has a type and we haven't set one yet, set it
    if (prompt.type && experimentType === 'undetermined') {
      onExperimentTypeChange?.(prompt.type);
    }
    sendMessage(prompt.prompt, null, prompt.type);
  };

  // Handle direct type selection via buttons
  const handleTypeSelection = (type: ExperimentType) => {
    onExperimentTypeChange?.(type);
    // Don't send a message, just update the type - the UI will change to show type-specific prompts
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
            {/* Show config diff for assistant messages with config updates */}
            {message.configDiff && (
              <div className="experiment-chat__config-diff">
                <div className="experiment-chat__config-diff-header">
                  <GitCompare size={14} />
                  <span>Config updated</span>
                </div>
                <pre className="experiment-chat__config-diff-content">
                  {message.configDiff}
                </pre>
              </div>
            )}
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

      {/* Type Selection Buttons (shown when type is undetermined) */}
      {messages.length <= 1 && experimentType === 'undetermined' && (
        <div className="experiment-chat__type-selection">
          <div className="experiment-chat__type-selection-label">
            Choose experiment type:
          </div>
          <div className="experiment-chat__type-selection-buttons">
            <button
              className="experiment-chat__type-btn experiment-chat__type-btn--tournament"
              onClick={() => handleTypeSelection('tournament')}
              type="button"
              disabled={loading}
            >
              <Beaker size={18} />
              <div className="experiment-chat__type-btn-content">
                <span className="experiment-chat__type-btn-title">Tournament</span>
                <span className="experiment-chat__type-btn-desc">Run AI players against each other</span>
              </div>
            </button>
            <button
              className="experiment-chat__type-btn experiment-chat__type-btn--replay"
              onClick={() => handleTypeSelection('replay')}
              type="button"
              disabled={loading}
            >
              <Repeat2 size={18} />
              <div className="experiment-chat__type-btn-content">
                <span className="experiment-chat__type-btn-title">Replay</span>
                <span className="experiment-chat__type-btn-desc">Re-run captured decisions</span>
              </div>
            </button>
          </div>
        </div>
      )}

      {/* Quick Prompts */}
      {messages.length <= 1 && quickPrompts.length > 0 && (
        <div className="experiment-chat__quick-prompts">
          <div className="experiment-chat__quick-prompts-label">
            <Sparkles size={14} />
            {experimentType === 'undetermined' ? 'Or start with a quick prompt:' : 'Quick Start'}
          </div>
          <div className="experiment-chat__quick-prompts-list">
            {quickPrompts.map((prompt) => (
              <button
                key={prompt.id}
                className={`experiment-chat__quick-prompt-btn ${prompt.type ? `experiment-chat__quick-prompt-btn--${prompt.type}` : ''}`}
                onClick={() => handleQuickPrompt(prompt)}
                type="button"
                disabled={loading}
              >
                {prompt.type === 'replay' && <Repeat2 size={12} />}
                {prompt.type === 'tournament' && <Beaker size={12} />}
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
