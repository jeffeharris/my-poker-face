import { useState, useEffect, useCallback } from 'react';
import { PageLayout, PageHeader } from '../../shared';
import { config } from '../../../config';
import type { PromptCapture, CaptureStats, CaptureFilters, ReplayResponse } from './types';
import './PromptDebugger.css';

interface PromptDebuggerProps {
  onBack: () => void;
}

export function PromptDebugger({ onBack }: PromptDebuggerProps) {
  const [captures, setCaptures] = useState<PromptCapture[]>([]);
  const [stats, setStats] = useState<CaptureStats | null>(null);
  const [selectedCapture, setSelectedCapture] = useState<PromptCapture | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [total, setTotal] = useState(0);

  // Filters
  const [filters, setFilters] = useState<CaptureFilters>({
    limit: 50,
    offset: 0,
  });

  // Replay state
  const [replayMode, setReplayMode] = useState(false);
  const [modifiedSystemPrompt, setModifiedSystemPrompt] = useState('');
  const [modifiedUserMessage, setModifiedUserMessage] = useState('');
  const [replayResult, setReplayResult] = useState<ReplayResponse | null>(null);
  const [replaying, setReplaying] = useState(false);

  const fetchCaptures = useCallback(async () => {
    setLoading(true);
    setError(null);

    try {
      const params = new URLSearchParams();
      if (filters.game_id) params.set('game_id', filters.game_id);
      if (filters.player_name) params.set('player_name', filters.player_name);
      if (filters.action) params.set('action', filters.action);
      if (filters.phase) params.set('phase', filters.phase);
      if (filters.min_pot_odds !== undefined) params.set('min_pot_odds', filters.min_pot_odds.toString());
      if (filters.limit) params.set('limit', filters.limit.toString());
      if (filters.offset) params.set('offset', filters.offset.toString());

      const response = await fetch(
        `${config.API_URL}/api/prompt-debug/captures?${params}`,
        { credentials: 'include' }
      );

      if (!response.ok) {
        throw new Error('Failed to fetch captures');
      }

      const data = await response.json();
      setCaptures(data.captures);
      setStats(data.stats);
      setTotal(data.total);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unknown error');
    } finally {
      setLoading(false);
    }
  }, [filters]);

  useEffect(() => {
    fetchCaptures();
  }, [fetchCaptures]);

  const fetchCaptureDetail = async (captureId: number) => {
    try {
      const response = await fetch(
        `${config.API_URL}/api/prompt-debug/captures/${captureId}`,
        { credentials: 'include' }
      );

      if (!response.ok) {
        throw new Error('Failed to fetch capture details');
      }

      const data = await response.json();
      setSelectedCapture(data.capture);
      setModifiedSystemPrompt(data.capture.system_prompt);
      setModifiedUserMessage(data.capture.user_message);
      setReplayMode(false);
      setReplayResult(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unknown error');
    }
  };

  const handleReplay = async () => {
    if (!selectedCapture) return;

    setReplaying(true);
    setError(null);

    try {
      const response = await fetch(
        `${config.API_URL}/api/prompt-debug/captures/${selectedCapture.id}/replay`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          credentials: 'include',
          body: JSON.stringify({
            system_prompt: modifiedSystemPrompt,
            user_message: modifiedUserMessage,
          }),
        }
      );

      if (!response.ok) {
        throw new Error('Replay failed');
      }

      const data = await response.json();
      setReplayResult(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unknown error');
    } finally {
      setReplaying(false);
    }
  };

  const formatPotOdds = (potOdds: number | null) => {
    if (potOdds === null) return '-';
    return `${potOdds.toFixed(1)}:1`;
  };

  const getActionColor = (action: string | null) => {
    switch (action) {
      case 'fold': return 'action-fold';
      case 'check': return 'action-check';
      case 'call': return 'action-call';
      case 'raise': return 'action-raise';
      case 'all_in': return 'action-allin';
      default: return '';
    }
  };

  const isSuspiciousFold = (capture: PromptCapture) => {
    return capture.action_taken === 'fold' && capture.pot_odds !== null && capture.pot_odds >= 5;
  };

  return (
    <PageLayout variant="top" glowColor="amber" maxWidth="xl">
      <PageHeader
        title="Prompt Debugger"
        subtitle="Analyze and replay AI decision prompts"
        onBack={onBack}
      />

      <div className="prompt-debugger">
        {/* Stats Summary */}
        {stats && (
          <div className="debugger-stats">
            <div className="stat-item">
              <span className="stat-value">{stats.total}</span>
              <span className="stat-label">Total Captures</span>
            </div>
            <div className="stat-item stat-warning">
              <span className="stat-value">{stats.suspicious_folds}</span>
              <span className="stat-label">Suspicious Folds</span>
            </div>
            {Object.entries(stats.by_action).map(([action, count]) => (
              <div key={action} className={`stat-item ${getActionColor(action)}`}>
                <span className="stat-value">{count}</span>
                <span className="stat-label">{action}</span>
              </div>
            ))}
          </div>
        )}

        {/* Filters */}
        <div className="debugger-filters">
          <select
            value={filters.action || ''}
            onChange={(e) => setFilters({ ...filters, action: e.target.value || undefined, offset: 0 })}
          >
            <option value="">All Actions</option>
            <option value="fold">Fold</option>
            <option value="check">Check</option>
            <option value="call">Call</option>
            <option value="raise">Raise</option>
          </select>

          <select
            value={filters.phase || ''}
            onChange={(e) => setFilters({ ...filters, phase: e.target.value || undefined, offset: 0 })}
          >
            <option value="">All Phases</option>
            <option value="PRE_FLOP">Pre-Flop</option>
            <option value="FLOP">Flop</option>
            <option value="TURN">Turn</option>
            <option value="RIVER">River</option>
          </select>

          <input
            type="number"
            placeholder="Min Pot Odds"
            value={filters.min_pot_odds || ''}
            onChange={(e) => setFilters({
              ...filters,
              min_pot_odds: e.target.value ? parseFloat(e.target.value) : undefined,
              offset: 0
            })}
          />

          <button onClick={() => setFilters({ limit: 50, offset: 0 })}>
            Clear Filters
          </button>
        </div>

        {error && <div className="debugger-error">{error}</div>}

        <div className="debugger-layout">
          {/* Capture List */}
          <div className="capture-list">
            <h3>Captures ({total})</h3>
            {loading && <div className="loading">Loading...</div>}

            {captures.map((capture) => (
              <div
                key={capture.id}
                className={`capture-item ${selectedCapture?.id === capture.id ? 'selected' : ''} ${isSuspiciousFold(capture) ? 'suspicious' : ''}`}
                onClick={() => fetchCaptureDetail(capture.id)}
              >
                <div className="capture-header">
                  <span className="capture-player">{capture.player_name}</span>
                  <span className={`capture-action ${getActionColor(capture.action_taken)}`}>
                    {capture.action_taken?.toUpperCase()}
                  </span>
                </div>
                <div className="capture-details">
                  <span className="capture-phase">{capture.phase}</span>
                  <span className="capture-pot">Pot: ${capture.pot_total}</span>
                  <span className="capture-odds">
                    {formatPotOdds(capture.pot_odds)} odds
                  </span>
                </div>
                {capture.player_hand && (
                  <div className="capture-hand">
                    {capture.player_hand.join(' ')}
                  </div>
                )}
                {isSuspiciousFold(capture) && (
                  <div className="suspicious-badge">Suspicious Fold</div>
                )}
              </div>
            ))}

            {/* Pagination */}
            {total > filters.limit! && (
              <div className="pagination">
                <button
                  disabled={filters.offset === 0}
                  onClick={() => setFilters({ ...filters, offset: Math.max(0, (filters.offset || 0) - filters.limit!) })}
                >
                  Previous
                </button>
                <span>
                  {Math.floor((filters.offset || 0) / filters.limit!) + 1} / {Math.ceil(total / filters.limit!)}
                </span>
                <button
                  disabled={(filters.offset || 0) + filters.limit! >= total}
                  onClick={() => setFilters({ ...filters, offset: (filters.offset || 0) + filters.limit! })}
                >
                  Next
                </button>
              </div>
            )}
          </div>

          {/* Detail Panel */}
          <div className="capture-detail">
            {selectedCapture ? (
              <>
                <div className="detail-header">
                  <h3>{selectedCapture.player_name} - {selectedCapture.phase}</h3>
                  <span className={`detail-action ${getActionColor(selectedCapture.action_taken)}`}>
                    {selectedCapture.action_taken?.toUpperCase()}
                    {selectedCapture.raise_amount && ` $${selectedCapture.raise_amount}`}
                  </span>
                </div>

                <div className="detail-context">
                  <div className="context-item">
                    <label>Hand:</label>
                    <span>{selectedCapture.player_hand?.join(' ') || '-'}</span>
                  </div>
                  <div className="context-item">
                    <label>Board:</label>
                    <span>{selectedCapture.community_cards?.join(' ') || '-'}</span>
                  </div>
                  <div className="context-item">
                    <label>Pot:</label>
                    <span>${selectedCapture.pot_total}</span>
                  </div>
                  <div className="context-item">
                    <label>Cost to Call:</label>
                    <span>${selectedCapture.cost_to_call}</span>
                  </div>
                  <div className="context-item highlight">
                    <label>Pot Odds:</label>
                    <span>{formatPotOdds(selectedCapture.pot_odds)}</span>
                  </div>
                  <div className="context-item">
                    <label>Stack:</label>
                    <span>${selectedCapture.player_stack}</span>
                  </div>
                </div>

                <div className="detail-tabs">
                  <button
                    className={!replayMode ? 'active' : ''}
                    onClick={() => setReplayMode(false)}
                  >
                    View
                  </button>
                  <button
                    className={replayMode ? 'active' : ''}
                    onClick={() => setReplayMode(true)}
                  >
                    Edit & Replay
                  </button>
                </div>

                {/* Token & Latency Info */}
                {(selectedCapture.input_tokens || selectedCapture.latency_ms) && (
                  <div className="token-info">
                    {selectedCapture.model && (
                      <span>
                        Model: {selectedCapture.model}
                        {selectedCapture.reasoning_effort && ` (${selectedCapture.reasoning_effort})`}
                      </span>
                    )}
                    {selectedCapture.input_tokens != null && (
                      <span>
                        In: {selectedCapture.input_tokens.toLocaleString()} tokens
                        {selectedCapture.cached_tokens != null && selectedCapture.cached_tokens > 0 && (
                          <span className="token-pct cached">
                            {' '}({Math.round((selectedCapture.cached_tokens / selectedCapture.input_tokens) * 100)}% cached)
                          </span>
                        )}
                      </span>
                    )}
                    {selectedCapture.output_tokens != null && (
                      <span>
                        Out: {selectedCapture.output_tokens.toLocaleString()} tokens
                        {selectedCapture.reasoning_tokens != null && selectedCapture.reasoning_tokens > 0 && (
                          <span className="token-pct reasoning">
                            {' '}({Math.round((selectedCapture.reasoning_tokens / selectedCapture.output_tokens) * 100)}% reasoning)
                          </span>
                        )}
                      </span>
                    )}
                    {selectedCapture.latency_ms && <span>Latency: {selectedCapture.latency_ms.toLocaleString()}ms</span>}
                    {selectedCapture.estimated_cost != null && (
                      <span className="cost">Cost: ${selectedCapture.estimated_cost.toFixed(4)}</span>
                    )}
                  </div>
                )}

                {!replayMode ? (
                  <div className="detail-prompts">
                    <div className="prompt-section">
                      <h4>System Prompt</h4>
                      <pre>{selectedCapture.system_prompt}</pre>
                    </div>

                    {/* Conversation History */}
                    {selectedCapture.conversation_history && selectedCapture.conversation_history.length > 0 && (
                      <div className="prompt-section conversation-history">
                        <h4>Conversation History ({selectedCapture.conversation_history.length} messages)</h4>
                        <div className="history-messages">
                          {selectedCapture.conversation_history.map((msg, idx) => (
                            <div key={idx} className={`history-message ${msg.role}`}>
                              <span className="message-role">{msg.role}</span>
                              <pre>{msg.content}</pre>
                            </div>
                          ))}
                        </div>
                      </div>
                    )}

                    <div className="prompt-section">
                      <h4>User Message (Current Turn)</h4>
                      <pre>{selectedCapture.user_message}</pre>
                    </div>
                    <div className="prompt-section">
                      <h4>AI Response</h4>
                      <pre>{selectedCapture.ai_response}</pre>
                    </div>

                    {/* Raw API Response - contains reasoning tokens, etc. */}
                    {selectedCapture.raw_api_response && (
                      <details className="prompt-section raw-response">
                        <summary>
                          <h4>Raw API Response (click to expand)</h4>
                        </summary>
                        <pre>{JSON.stringify(JSON.parse(selectedCapture.raw_api_response), null, 2)}</pre>
                      </details>
                    )}
                  </div>
                ) : (
                  <div className="replay-editor">
                    <div className="prompt-section">
                      <h4>System Prompt (editable)</h4>
                      <textarea
                        value={modifiedSystemPrompt}
                        onChange={(e) => setModifiedSystemPrompt(e.target.value)}
                        rows={10}
                      />
                    </div>
                    <div className="prompt-section">
                      <h4>User Message (editable)</h4>
                      <textarea
                        value={modifiedUserMessage}
                        onChange={(e) => setModifiedUserMessage(e.target.value)}
                        rows={15}
                      />
                    </div>
                    <button
                      className="replay-button"
                      onClick={handleReplay}
                      disabled={replaying}
                    >
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
                          Model: {replayResult.model_used}
                          {replayResult.latency_ms && ` | ${replayResult.latency_ms}ms`}
                        </div>
                      </div>
                    )}
                  </div>
                )}
              </>
            ) : (
              <div className="no-selection">
                Select a capture from the list to view details
              </div>
            )}
          </div>
        </div>
      </div>
    </PageLayout>
  );
}
