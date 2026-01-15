/**
 * Prompt Playground component for viewing and replaying captured LLM prompts.
 *
 * This is similar to PromptDebugger but works with any captured prompt
 * (not just poker game decisions).
 */
import { useState, useEffect, useCallback } from 'react';
import { config } from '../../../config';
import type {
  PlaygroundCapture,
  PlaygroundCaptureDetail,
  PlaygroundStats,
  PlaygroundFilters,
  PlaygroundMode,
  ReplayResponse,
} from './types';
import { TemplateEditor } from './TemplateEditor';
import './PromptPlayground.css';

interface Props {
  onBack: () => void;
}

export function PromptPlayground({ onBack }: Props) {
  // State
  const [captures, setCaptures] = useState<PlaygroundCapture[]>([]);
  const [selectedCapture, setSelectedCapture] = useState<PlaygroundCaptureDetail | null>(null);
  const [stats, setStats] = useState<PlaygroundStats | null>(null);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Filters
  const [filters, setFilters] = useState<PlaygroundFilters>({
    limit: 50,
    offset: 0,
  });

  // Mode (view or replay)
  const [mode, setMode] = useState<PlaygroundMode>('view');

  // Panel expansion state (for mobile)
  const [listCollapsed, setListCollapsed] = useState(false);

  // Replay state
  const [replayProvider, setReplayProvider] = useState('openai');
  const [replayModel, setReplayModel] = useState('');
  const [replayEffort, setReplayEffort] = useState('minimal');
  const [modifiedSystemPrompt, setModifiedSystemPrompt] = useState('');
  const [modifiedUserMessage, setModifiedUserMessage] = useState('');
  const [replayResult, setReplayResult] = useState<ReplayResponse | null>(null);
  const [replaying, setReplaying] = useState(false);

  // Available providers/models
  const [providers, setProviders] = useState<Array<{ id: string; name: string; models: string[]; model_tiers?: Record<string, string> }>>([]);

  // Fetch captures
  const fetchCaptures = useCallback(async () => {
    setLoading(true);
    setError(null);

    try {
      const params = new URLSearchParams();
      if (filters.call_type) params.append('call_type', filters.call_type);
      if (filters.provider) params.append('provider', filters.provider);
      if (filters.limit) params.append('limit', String(filters.limit));
      if (filters.offset) params.append('offset', String(filters.offset));

      const response = await fetch(`${config.API_URL}/analytics/api/playground/captures?${params}`);
      const data = await response.json();

      if (data.success) {
        setCaptures(data.captures);
        setTotal(data.total);
        setStats(data.stats);
      } else {
        setError(data.error || 'Failed to fetch captures');
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to fetch captures');
    } finally {
      setLoading(false);
    }
  }, [filters]);

  // Fetch single capture details
  const fetchCaptureDetail = async (id: number) => {
    try {
      const response = await fetch(`${config.API_URL}/analytics/api/playground/captures/${id}`);
      const data = await response.json();

      if (data.success) {
        setSelectedCapture(data.capture);
        setModifiedSystemPrompt(data.capture.system_prompt || '');
        setModifiedUserMessage(data.capture.user_message || '');
        setReplayProvider(data.capture.provider || 'openai');
        setReplayModel(data.capture.model || '');
        setReplayEffort(data.capture.reasoning_effort || 'minimal');
        setReplayResult(null);
      }
    } catch (err) {
      console.error('Failed to fetch capture detail:', err);
    }
  };

  // Fetch providers
  const fetchProviders = async () => {
    try {
      const response = await fetch(`${config.API_URL}/api/llm-providers`);
      const data = await response.json();
      if (data.providers) {
        setProviders(data.providers);
      }
    } catch (err) {
      console.error('Failed to fetch providers:', err);
    }
  };

  // Replay prompt
  const handleReplay = async () => {
    if (!selectedCapture) return;

    setReplaying(true);
    setReplayResult(null);

    try {
      const response = await fetch(
        `${config.API_URL}/analytics/api/playground/captures/${selectedCapture.id}/replay`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            system_prompt: modifiedSystemPrompt,
            user_message: modifiedUserMessage,
            provider: replayProvider,
            model: replayModel || undefined,
            reasoning_effort: replayEffort,
            use_history: true,
          }),
        }
      );
      const data = await response.json();

      if (data.success) {
        setReplayResult(data);
      } else {
        setReplayResult({ ...data, success: false, error: data.error });
      }
    } catch (err) {
      setReplayResult({
        success: false,
        error: err instanceof Error ? err.message : 'Replay failed',
        original_response: '',
        new_response: '',
        provider_used: '',
        model_used: '',
        input_tokens: 0,
        output_tokens: 0,
        latency_ms: null,
      });
    } finally {
      setReplaying(false);
    }
  };

  // Initial fetch
  useEffect(() => {
    fetchCaptures();
    fetchProviders();
  }, [fetchCaptures]);

  // Get models for selected provider
  const currentProvider = providers.find(p => p.id === replayProvider);
  const providerModels = currentProvider?.models || [];

  // Format model label with cost tier
  const formatModelLabel = (model: string): string => {
    const tier = currentProvider?.model_tiers?.[model] || '';
    return tier ? `${model} (${tier})` : model;
  };

  return (
    <div className="playground-container">
      {/* Header */}
      <div className="playground-header">
        <button className="back-button" onClick={onBack}>
          &larr; Back
        </button>
        <h1>Prompt Playground</h1>
        {stats && (
          <div className="stats-summary">
            <span className="stat-badge">{stats.total} captures</span>
            {Object.entries(stats.by_call_type).slice(0, 3).map(([type, count]) => (
              <span key={type} className="stat-badge type-badge">{type}: {count}</span>
            ))}
          </div>
        )}
      </div>

      {/* Main content */}
      <div className="playground-content">
        {/* Left panel - Capture list */}
        <div className={`capture-list-panel ${listCollapsed ? 'collapsed' : ''}`}>
          {/* Filters */}
          <div className="filters">
            <select
              value={filters.call_type || ''}
              onChange={(e) => setFilters({ ...filters, call_type: e.target.value || undefined, offset: 0 })}
            >
              <option value="">All call types</option>
              {stats?.by_call_type && Object.keys(stats.by_call_type).map(type => (
                <option key={type} value={type}>{type}</option>
              ))}
            </select>
            <select
              value={filters.provider || ''}
              onChange={(e) => setFilters({ ...filters, provider: e.target.value || undefined, offset: 0 })}
            >
              <option value="">All providers</option>
              {stats?.by_provider && Object.keys(stats.by_provider).map(provider => (
                <option key={provider} value={provider}>{provider}</option>
              ))}
            </select>
            <button onClick={fetchCaptures} disabled={loading}>
              {loading ? 'Loading...' : 'Refresh'}
            </button>
          </div>

          {/* Error display */}
          {error && <div className="error-message">{error}</div>}

          {/* Capture list */}
          <div className="capture-list">
            {captures.map(capture => (
              <div
                key={capture.id}
                className={`capture-item ${selectedCapture?.id === capture.id ? 'selected' : ''}`}
                onClick={() => fetchCaptureDetail(capture.id)}
              >
                <div className="capture-header">
                  <span className="call-type">{capture.call_type}</span>
                  <span className="timestamp">
                    {new Date(capture.created_at).toLocaleString()}
                  </span>
                </div>
                <div className="capture-meta">
                  <span className="provider">{capture.provider}</span>
                  <span className="model">{capture.model}</span>
                  {capture.latency_ms && (
                    <span className="latency">{capture.latency_ms}ms</span>
                  )}
                </div>
              </div>
            ))}
            {captures.length === 0 && !loading && (
              <div className="no-captures">
                No captures found. Enable capture with LLM_PROMPT_CAPTURE=all
              </div>
            )}
          </div>

          {/* Pagination */}
          {total > (filters.limit || 50) && (
            <div className="pagination">
              <button
                disabled={(filters.offset || 0) === 0}
                onClick={() => setFilters({ ...filters, offset: Math.max(0, (filters.offset || 0) - (filters.limit || 50)) })}
              >
                Previous
              </button>
              <span>
                {(filters.offset || 0) + 1} - {Math.min((filters.offset || 0) + (filters.limit || 50), total)} of {total}
              </span>
              <button
                disabled={(filters.offset || 0) + (filters.limit || 50) >= total}
                onClick={() => setFilters({ ...filters, offset: (filters.offset || 0) + (filters.limit || 50) })}
              >
                Next
              </button>
            </div>
          )}
        </div>

        {/* Right panel - Detail view */}
        <div className={`capture-detail-panel ${listCollapsed ? 'expanded' : ''}`}>
          {/* Mode tabs - always visible */}
          <div className="detail-header">
            <button
              className="expand-toggle"
              onClick={() => setListCollapsed(!listCollapsed)}
              title={listCollapsed ? 'Show capture list' : 'Expand detail view'}
            >
              {listCollapsed ? (
                <>
                  <span className="toggle-icon">&#9664;</span>
                  <span className="toggle-text">List</span>
                </>
              ) : (
                <>
                  <span className="toggle-icon">&#9654;</span>
                  <span className="toggle-text">Expand</span>
                </>
              )}
            </button>
            {selectedCapture && mode !== 'templates' && (
              <div className="capture-title">
                <span className="capture-type">{selectedCapture.call_type}</span>
                <span className="capture-model">{selectedCapture.model}</span>
              </div>
            )}
            {mode === 'templates' && (
              <div className="capture-title">
                <span className="capture-type">Template Editor</span>
              </div>
            )}
          </div>
          <div className="mode-tabs">
            <button
              className={mode === 'view' ? 'active' : ''}
              onClick={() => setMode('view')}
              disabled={!selectedCapture}
              title={!selectedCapture ? 'Select a capture first' : ''}
            >
              View
            </button>
            <button
              className={mode === 'replay' ? 'active' : ''}
              onClick={() => setMode('replay')}
              disabled={!selectedCapture}
              title={!selectedCapture ? 'Select a capture first' : ''}
            >
              Replay
            </button>
            <button
              className={mode === 'templates' ? 'active' : ''}
              onClick={() => setMode('templates')}
            >
              Templates
            </button>
          </div>

          {mode === 'templates' ? (
            /* Templates mode - always available */
            <TemplateEditor
              onNavigateToCapture={(captureId) => {
                fetchCaptureDetail(captureId);
                setMode('replay');
              }}
            />
          ) : selectedCapture ? (
            mode === 'view' ? (
                /* View mode */
                <div className="view-mode">
                  <div className="prompt-section">
                    <h3>System Prompt</h3>
                    <pre className="prompt-content">{selectedCapture.system_prompt}</pre>
                  </div>

                  {selectedCapture.conversation_history && selectedCapture.conversation_history.length > 0 && (
                    <div className="prompt-section">
                      <h3>Conversation History ({selectedCapture.conversation_history.length} messages)</h3>
                      <div className="history-list">
                        {selectedCapture.conversation_history.map((msg, i) => (
                          <div key={i} className={`history-message ${msg.role}`}>
                            <span className="role-label">{msg.role}</span>
                            <pre>{msg.content}</pre>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}

                  <div className="prompt-section">
                    <h3>User Message</h3>
                    <pre className="prompt-content">{selectedCapture.user_message}</pre>
                  </div>

                  <div className="prompt-section">
                    <h3>AI Response</h3>
                    <pre className="prompt-content response">{selectedCapture.ai_response}</pre>
                  </div>

                  <div className="metrics">
                    <span>Provider: {selectedCapture.provider}</span>
                    <span>Model: {selectedCapture.model}</span>
                    <span>Latency: {selectedCapture.latency_ms}ms</span>
                    <span>Input: {selectedCapture.input_tokens} tokens</span>
                    <span>Output: {selectedCapture.output_tokens} tokens</span>
                    {selectedCapture.estimated_cost && (
                      <span>Cost: ${selectedCapture.estimated_cost.toFixed(6)}</span>
                    )}
                  </div>
                </div>
              ) : (
                /* Replay mode */
                <div className="replay-mode">
                  <div className="replay-controls">
                    <div className="control-row">
                      <label>Provider</label>
                      <select
                        value={replayProvider}
                        onChange={(e) => {
                          setReplayProvider(e.target.value);
                          setReplayModel('');
                        }}
                      >
                        {providers.map(p => (
                          <option key={p.id} value={p.id}>{p.name}</option>
                        ))}
                      </select>
                    </div>
                    <div className="control-row">
                      <label>Model</label>
                      <select
                        value={replayModel}
                        onChange={(e) => setReplayModel(e.target.value)}
                      >
                        <option value="">Default</option>
                        {providerModels.map(m => (
                          <option key={m} value={m}>{formatModelLabel(m)}</option>
                        ))}
                      </select>
                    </div>
                    <div className="control-row">
                      <label>Reasoning</label>
                      <select
                        value={replayEffort}
                        onChange={(e) => setReplayEffort(e.target.value)}
                      >
                        <option value="minimal">Minimal</option>
                        <option value="low">Low</option>
                        <option value="medium">Medium</option>
                        <option value="high">High</option>
                      </select>
                    </div>
                  </div>

                  <div className="prompt-section">
                    <h3>System Prompt</h3>
                    <textarea
                      value={modifiedSystemPrompt}
                      onChange={(e) => setModifiedSystemPrompt(e.target.value)}
                      rows={6}
                    />
                  </div>

                  <div className="prompt-section">
                    <h3>User Message</h3>
                    <textarea
                      value={modifiedUserMessage}
                      onChange={(e) => setModifiedUserMessage(e.target.value)}
                      rows={8}
                    />
                  </div>

                  <button
                    className="replay-button"
                    onClick={handleReplay}
                    disabled={replaying}
                  >
                    {replaying ? 'Replaying...' : replayResult ? 'Replay Again' : 'Replay with Changes'}
                  </button>

                  {replayResult && (
                    <div className={`replay-result ${replayResult.success ? '' : 'error'}`}>
                      <div className="replay-result-header">
                        <span className="result-label">Result</span>
                        <button
                          className="clear-result-btn"
                          onClick={() => setReplayResult(null)}
                          title="Clear result"
                        >
                          ×
                        </button>
                      </div>
                      {replayResult.error ? (
                        <div className="error-message">{replayResult.error}</div>
                      ) : (
                        <>
                          <div className="comparison">
                            <div className="original">
                              <h4>Original Response</h4>
                              <pre>{replayResult.original_response}</pre>
                            </div>
                            <div className="new">
                              <h4>New Response ({replayResult.model_used})</h4>
                              <pre>{replayResult.new_response}</pre>
                            </div>
                          </div>
                          <div className="replay-metrics">
                            <span>Provider: {replayResult.provider_used}</span>
                            <span>Model: {replayResult.model_used}</span>
                            <span>Latency: {replayResult.latency_ms}ms</span>
                            <span>Input: {replayResult.input_tokens} tokens</span>
                            <span>Output: {replayResult.output_tokens} tokens</span>
                          </div>
                        </>
                      )}
                    </div>
                  )}
                </div>
              )
            ) : (
              <div className="no-selection">
                Select a capture to view details
              </div>
            )}
        </div>
      </div>
    </div>
  );
}

export default PromptPlayground;
