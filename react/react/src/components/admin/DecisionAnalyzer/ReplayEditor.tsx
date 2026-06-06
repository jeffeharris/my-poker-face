import type { ConversationMessage, ReplayResponse, ProviderInfo } from './types';

interface ReplayEditorProps {
  modifiedSystemPrompt: string;
  onSystemPromptChange: (value: string) => void;
  modifiedUserMessage: string;
  onUserMessageChange: (value: string) => void;
  modifiedConversationHistory: ConversationMessage[];
  onUpdateHistoryMessage: (index: number, field: 'role' | 'content', value: string) => void;
  onRemoveHistoryMessage: (index: number) => void;
  onAddHistoryMessage: () => void;
  useHistory: boolean;
  onUseHistoryChange: (value: boolean) => void;
  replayProvider: string;
  onReplayProviderChange: (provider: string) => void;
  replayModel: string;
  onReplayModelChange: (model: string) => void;
  replayReasoningEffort: string;
  onReplayReasoningEffortChange: (effort: string) => void;
  providers: ProviderInfo[];
  getModelsForProvider: (provider: string) => string[];
  reasoningLevels: string[];
  onReplay: () => void;
  replaying: boolean;
  replayResult: ReplayResponse | null;
}

// "Edit & Replay" mode: editable prompts/history + provider settings, plus
// the original-vs-new response comparison once a replay completes.
export function ReplayEditor({
  modifiedSystemPrompt,
  onSystemPromptChange,
  modifiedUserMessage,
  onUserMessageChange,
  modifiedConversationHistory,
  onUpdateHistoryMessage,
  onRemoveHistoryMessage,
  onAddHistoryMessage,
  useHistory,
  onUseHistoryChange,
  replayProvider,
  onReplayProviderChange,
  replayModel,
  onReplayModelChange,
  replayReasoningEffort,
  onReplayReasoningEffortChange,
  providers,
  getModelsForProvider,
  reasoningLevels,
  onReplay,
  replaying,
  replayResult,
}: ReplayEditorProps) {
  return (
    <div className="replay-editor">
      <div className="prompt-section">
        <h4>System Prompt (editable)</h4>
        <textarea
          value={modifiedSystemPrompt}
          onChange={(e) => onSystemPromptChange(e.target.value)}
          rows={10}
        />
      </div>

      {/* Conversation History Editor */}
      <div className="prompt-section conversation-history-editor">
        <div className="history-header">
          <h4>Conversation History ({modifiedConversationHistory.length} messages)</h4>
          <label className="history-toggle">
            <input
              type="checkbox"
              checked={useHistory}
              onChange={(e) => onUseHistoryChange(e.target.checked)}
            />
            Include in replay
          </label>
        </div>

        {useHistory && (
          <div className="history-editor">
            {modifiedConversationHistory.map((msg, idx) => (
              <div key={idx} className="history-message-editor">
                <select
                  value={msg.role}
                  onChange={(e) => onUpdateHistoryMessage(idx, 'role', e.target.value)}
                >
                  <option value="user">user</option>
                  <option value="assistant">assistant</option>
                  <option value="system">system</option>
                </select>
                <textarea
                  value={msg.content}
                  onChange={(e) => onUpdateHistoryMessage(idx, 'content', e.target.value)}
                  rows={3}
                  placeholder="Message content..."
                />
                <button
                  className="remove-message"
                  onClick={() => onRemoveHistoryMessage(idx)}
                  title="Remove message"
                >
                  ×
                </button>
              </div>
            ))}
            <button className="add-message" onClick={onAddHistoryMessage}>
              + Add Message
            </button>
          </div>
        )}

        {!useHistory && modifiedConversationHistory.length > 0 && (
          <div className="history-disabled-notice">
            {modifiedConversationHistory.length} message(s) will be excluded from replay
          </div>
        )}
      </div>

      <div className="prompt-section">
        <h4>User Message (editable)</h4>
        <textarea
          value={modifiedUserMessage}
          onChange={(e) => onUserMessageChange(e.target.value)}
          rows={15}
        />
      </div>

      {/* Provider, Model, and Reasoning Settings */}
      <div className="replay-settings">
        <div className="setting-group">
          <label>Provider:</label>
          <select value={replayProvider} onChange={(e) => onReplayProviderChange(e.target.value)}>
            {providers.length > 0 ? (
              providers.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))
            ) : (
              <option value="openai">OpenAI</option>
            )}
          </select>
        </div>
        <div className="setting-group">
          <label>Model:</label>
          <select value={replayModel} onChange={(e) => onReplayModelChange(e.target.value)}>
            {getModelsForProvider(replayProvider).map((model) => (
              <option key={model} value={model}>
                {model}
              </option>
            ))}
          </select>
        </div>
        <div className="setting-group">
          <label>Reasoning:</label>
          <select
            value={replayReasoningEffort}
            onChange={(e) => onReplayReasoningEffortChange(e.target.value)}
          >
            {reasoningLevels.map((level) => (
              <option key={level} value={level}>
                {level}
              </option>
            ))}
          </select>
        </div>
      </div>

      <button className="replay-button" onClick={onReplay} disabled={replaying}>
        {replaying ? 'Replaying...' : 'Replay with Changes'}
      </button>

      {replayResult && (
        <div className="replay-results">
          <div className="replay-comparison">
            <div className="comparison-side">
              <h4>Original Response</h4>
              <pre>{replayResult.original_response}</pre>
            </div>
            <div className="comparison-side">
              <h4>New Response</h4>
              <pre>{replayResult.new_response}</pre>
            </div>
          </div>
          <div className="replay-meta">
            <strong>{replayResult.provider_used}</strong> / {replayResult.model_used}
            {replayResult.reasoning_effort_used && ` (${replayResult.reasoning_effort_used})`}
            {replayResult.latency_ms && ` | ${replayResult.latency_ms}ms`}
            {replayResult.messages_count && ` | ${replayResult.messages_count} messages`}
            {replayResult.used_history !== undefined && (
              <span className={replayResult.used_history ? 'history-used' : 'history-skipped'}>
                {replayResult.used_history ? ' | History included' : ' | History excluded'}
              </span>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
