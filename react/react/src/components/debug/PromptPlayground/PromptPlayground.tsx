/**
 * Prompt Playground component for viewing and replaying captured LLM prompts.
 *
 * This is similar to PromptDebugger but works with any captured prompt
 * (not just poker game decisions).
 */
import { useState, useEffect, useCallback } from 'react';
import { config } from '../../../config';
import { useLLMProviders } from '../../../hooks/useLLMProviders';
import { AvatarAssignmentModal } from './AvatarAssignmentModal';
import type {
  PlaygroundCapture,
  PlaygroundCaptureDetail,
  PlaygroundStats,
  PlaygroundFilters,
  ReplayResponse,
  ImageReplayResponse,
  ImageProvider,
} from './types';
import './PromptPlayground.css';

interface Props {
  onBack?: () => void;
  embedded?: boolean;
}

export function PromptPlayground({ onBack, embedded = false }: Props) {
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
  const [mode, setMode] = useState<'view' | 'replay'>('view');

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

  // Image replay state
  const [imageProviders, setImageProviders] = useState<ImageProvider[]>([]);
  const [imageReplayProvider, setImageReplayProvider] = useState('pollinations');
  const [imageReplayModel, setImageReplayModel] = useState('');
  const [imageReplaySize, setImageReplaySize] = useState('512x512');
  const [modifiedImagePrompt, setModifiedImagePrompt] = useState('');
  const [imageReplayResult, setImageReplayResult] = useState<ImageReplayResponse | null>(null);

  // Avatar assignment modal state
  const [showAvatarModal, setShowAvatarModal] = useState(false);
  const [avatarImageUrl, setAvatarImageUrl] = useState<string | null>(null);

  // Available providers/models (using 'system' scope for admin tools)
  const { providers, getModelsForProvider, getModelTier } = useLLMProviders({ scope: 'system' });

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

      const response = await fetch(`${config.API_URL}/admin/api/playground/captures?${params}`);
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

  // Fetch image providers
  const fetchImageProviders = useCallback(async () => {
    try {
      const response = await fetch(`${config.API_URL}/admin/api/image-providers`);
      const data = await response.json();
      if (data.success) {
        setImageProviders(data.providers || []);
      }
    } catch (err) {
      console.error('Failed to fetch image providers:', err);
    }
  }, []);

  // Fetch single capture details
  const fetchCaptureDetail = async (id: number) => {
    try {
      const response = await fetch(`${config.API_URL}/admin/api/playground/captures/${id}`);
      const data = await response.json();

      if (data.success) {
        const capture = data.capture;
        setSelectedCapture(capture);

        // Check if it's an image capture
        if (capture.is_image_capture) {
          // Set image-specific state
          setModifiedImagePrompt(capture.image_prompt || capture.user_message || '');
          setImageReplayProvider(capture.provider || 'pollinations');
          setImageReplayModel(capture.model || '');
          setImageReplaySize(capture.image_size || '512x512');
          setImageReplayResult(null);
        } else {
          // Set text-specific state
          setModifiedSystemPrompt(capture.system_prompt || '');
          setModifiedUserMessage(capture.user_message || '');
          setReplayProvider(capture.provider || 'openai');
          setReplayModel(capture.model || '');
          setReplayEffort(capture.reasoning_effort || 'minimal');
          setReplayResult(null);
        }
      }
    } catch (err) {
      console.error('Failed to fetch capture detail:', err);
    }
  };

  // Replay prompt
  const handleReplay = async () => {
    if (!selectedCapture) return;

    setReplaying(true);
    setReplayResult(null);

    try {
      const response = await fetch(
        `${config.API_URL}/admin/api/playground/captures/${selectedCapture.id}/replay`,
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

  // Image replay handler
  const handleImageReplay = async () => {
    if (!selectedCapture || !selectedCapture.is_image_capture) return;

    setReplaying(true);
    setImageReplayResult(null);

    try {
      const response = await fetch(
        `${config.API_URL}/admin/api/playground/captures/${selectedCapture.id}/replay-image`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            prompt: modifiedImagePrompt,
            provider: imageReplayProvider,
            model: imageReplayModel || undefined,
            size: imageReplaySize,
          }),
        }
      );
      const data = await response.json();

      if (data.success) {
        setImageReplayResult(data);
      } else {
        setImageReplayResult({ ...data, success: false, error: data.error });
      }
    } catch (err) {
      setImageReplayResult({
        success: false,
        error: err instanceof Error ? err.message : 'Image replay failed',
        original_image_url: null,
        new_image_url: null,
        provider_used: '',
        model_used: '',
        latency_ms: null,
        size_used: '',
      });
    } finally {
      setReplaying(false);
    }
  };

  // Avatar assignment handler
  const handleAssignAvatar = async (personality: string, emotion: string) => {
    if (!selectedCapture) throw new Error('No capture selected');

    const response = await fetch(
      `${config.API_URL}/admin/api/playground/captures/${selectedCapture.id}/assign-avatar`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          personality_name: personality,
          emotion: emotion,
          use_replayed: !!avatarImageUrl && avatarImageUrl !== selectedCapture.image_url,
          replayed_image_data: avatarImageUrl !== selectedCapture.image_url ? avatarImageUrl : undefined,
        }),
      }
    );

    const data = await response.json();
    if (!data.success) {
      throw new Error(data.error || 'Failed to assign avatar');
    }
  };

  // Open avatar modal with the appropriate image
  const openAvatarModal = (imageUrl: string) => {
    setAvatarImageUrl(imageUrl);
    setShowAvatarModal(true);
  };

  // Initial fetch
  useEffect(() => {
    fetchCaptures();
    fetchImageProviders();
  }, [fetchCaptures, fetchImageProviders]);

  // Get models for selected provider (with fallback)
  const providerModels = getModelsForProvider(replayProvider);

  // Format model label with cost tier
  const formatModelLabel = (model: string): string => {
    const tier = getModelTier(replayProvider, model);
    return tier ? `${model} (${tier})` : model;
  };

  return (
    <div className={`playground-container ${embedded ? 'playground-container--embedded' : ''}`}>
      {/* Header */}
      <div className="playground-header">
        {!embedded && onBack && (
          <button className="back-button" onClick={onBack}>
            &larr; Back
          </button>
        )}
        {!embedded && <h1>Prompt Playground</h1>}
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
            {selectedCapture && (
              <div className="capture-title">
                <span className="capture-type">{selectedCapture.call_type}</span>
                <span className="capture-model">{selectedCapture.model}</span>
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
          </div>

          {selectedCapture ? (
            mode === 'view' ? (
                /* View mode - conditional for text vs image */
                selectedCapture.is_image_capture ? (
                  /* Image View Mode */
                  <div className="view-mode image-view-mode">
                    <div className="image-preview-section">
                      <h3>Generated Image</h3>
                      <div className="image-preview-container">
                        {selectedCapture.image_url || selectedCapture.image_data ? (
                          <img
                            src={selectedCapture.image_url || `data:image/png;base64,${selectedCapture.image_data}`}
                            alt="Generated"
                            className="captured-image"
                          />
                        ) : (
                          <div className="no-image">Image data not available</div>
                        )}
                      </div>
                    </div>

                    <div className="prompt-section">
                      <h3>Image Prompt</h3>
                      <pre className="prompt-content">{selectedCapture.image_prompt || selectedCapture.user_message}</pre>
                    </div>

                    {(selectedCapture.target_personality || selectedCapture.target_emotion) && (
                      <div className="prompt-section">
                        <h3>Target</h3>
                        <div className="target-info">
                          {selectedCapture.target_personality && (
                            <span className="target-badge">Personality: {selectedCapture.target_personality}</span>
                          )}
                          {selectedCapture.target_emotion && (
                            <span className="target-badge">Emotion: {selectedCapture.target_emotion}</span>
                          )}
                        </div>
                      </div>
                    )}

                    <div className="metrics">
                      <span>Provider: {selectedCapture.provider}</span>
                      <span>Model: {selectedCapture.model}</span>
                      <span>Size: {selectedCapture.image_size || 'unknown'}</span>
                      {selectedCapture.image_width && selectedCapture.image_height && (
                        <span>Dimensions: {selectedCapture.image_width}x{selectedCapture.image_height}</span>
                      )}
                      <span>Latency: {selectedCapture.latency_ms}ms</span>
                    </div>

                    {selectedCapture.image_url && (
                      <button
                        className="assign-avatar-btn"
                        onClick={() => openAvatarModal(selectedCapture.image_url!)}
                      >
                        Assign as Avatar
                      </button>
                    )}
                  </div>
                ) : (
                  /* Text View Mode */
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
                )
              ) : (
                /* Replay mode - conditional for text vs image */
                selectedCapture.is_image_capture ? (
                  /* Image Replay Mode */
                  <div className="replay-mode image-replay-mode">
                    <div className="replay-controls">
                      <div className="control-row">
                        <label>Provider</label>
                        <select
                          value={imageReplayProvider}
                          onChange={(e) => {
                            setImageReplayProvider(e.target.value);
                            setImageReplayModel('');
                          }}
                        >
                          {imageProviders.map(p => (
                            <option key={p.id} value={p.id}>{p.name}</option>
                          ))}
                        </select>
                      </div>
                      <div className="control-row">
                        <label>Model</label>
                        <select
                          value={imageReplayModel}
                          onChange={(e) => setImageReplayModel(e.target.value)}
                        >
                          <option value="">Default</option>
                          {imageProviders.find(p => p.id === imageReplayProvider)?.models.map(m => (
                            <option key={m.id} value={m.id}>{m.name}</option>
                          ))}
                        </select>
                      </div>
                      <div className="control-row">
                        <label>Size</label>
                        <select
                          value={imageReplaySize}
                          onChange={(e) => setImageReplaySize(e.target.value)}
                        >
                          {(imageProviders.find(p => p.id === imageReplayProvider)?.size_presets || [
                            { label: '512x512', value: '512x512', cost: '$' },
                            { label: '1024x1024', value: '1024x1024', cost: '$$' },
                          ]).map(preset => (
                            <option key={preset.value} value={preset.value}>
                              {preset.label} ({preset.cost})
                            </option>
                          ))}
                        </select>
                      </div>
                    </div>

                    <div className="prompt-section">
                      <h3>Image Prompt</h3>
                      <textarea
                        value={modifiedImagePrompt}
                        onChange={(e) => setModifiedImagePrompt(e.target.value)}
                        rows={6}
                      />
                    </div>

                    <button
                      className="replay-button"
                      onClick={handleImageReplay}
                      disabled={replaying}
                    >
                      {replaying ? 'Generating...' : imageReplayResult ? 'Generate Again' : 'Generate Image'}
                    </button>

                    {imageReplayResult && (
                      <div className={`replay-result ${imageReplayResult.success ? '' : 'error'}`}>
                        <div className="replay-result-header">
                          <span className="result-label">Result</span>
                          <button
                            className="clear-result-btn"
                            onClick={() => setImageReplayResult(null)}
                            title="Clear result"
                          >
                            ×
                          </button>
                        </div>
                        {imageReplayResult.error ? (
                          <div className="error-message">{imageReplayResult.error}</div>
                        ) : (
                          <>
                            <div className="image-comparison">
                              <div className="original">
                                <h4>Original Image</h4>
                                {imageReplayResult.original_image_url ? (
                                  <img src={imageReplayResult.original_image_url} alt="Original" />
                                ) : (
                                  <div className="no-image">Not available</div>
                                )}
                              </div>
                              <div className="new">
                                <h4>New Image ({imageReplayResult.model_used})</h4>
                                {imageReplayResult.new_image_url ? (
                                  <img src={imageReplayResult.new_image_url} alt="New" />
                                ) : (
                                  <div className="no-image">Not available</div>
                                )}
                              </div>
                            </div>
                            <div className="replay-metrics">
                              <span>Provider: {imageReplayResult.provider_used}</span>
                              <span>Model: {imageReplayResult.model_used}</span>
                              <span>Size: {imageReplayResult.size_used}</span>
                              <span>Latency: {imageReplayResult.latency_ms}ms</span>
                            </div>
                            {imageReplayResult.new_image_url && (
                              <button
                                className="assign-avatar-btn"
                                onClick={() => openAvatarModal(imageReplayResult.new_image_url!)}
                              >
                                Assign New Image as Avatar
                              </button>
                            )}
                          </>
                        )}
                      </div>
                    )}
                  </div>
                ) : (
                  /* Text Replay Mode */
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
              )
            ) : (
              <div className="no-selection">
                Select a capture to view details
              </div>
            )}
        </div>
      </div>

      {/* Avatar Assignment Modal */}
      {showAvatarModal && avatarImageUrl && selectedCapture && (
        <AvatarAssignmentModal
          imageUrl={avatarImageUrl}
          defaultPersonality={selectedCapture.target_personality}
          defaultEmotion={selectedCapture.target_emotion}
          captureId={selectedCapture.id}
          onAssign={handleAssignAvatar}
          onClose={() => {
            setShowAvatarModal(false);
            setAvatarImageUrl(null);
          }}
        />
      )}
    </div>
  );
}

export default PromptPlayground;
