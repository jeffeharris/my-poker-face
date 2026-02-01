import { useState, useEffect, useCallback } from 'react';
import { RefreshCw, ChevronLeft, ChevronRight } from 'lucide-react';
import { PageLayout, PageHeader } from '../../shared';
import { config } from '../../../config';
import { useLLMProviders } from '../../../hooks/useLLMProviders';
import { useViewport } from '../../../hooks/useViewport';
import { logger } from '../../../utils/logger';
import { MobileFilterSheet } from '../shared/MobileFilterSheet';
import { MobileFilterBar } from '../shared/MobileFilterBar';
import { FilterSheetContent } from '../shared/FilterSheetContent';
import { FilterGroup } from '../shared/FilterGroup';
import type { PromptCapture, CaptureStats, CaptureFilters, ReplayResponse, DecisionAnalysisStats, ConversationMessage, DecisionAnalysis, DebugMode, InterrogationMessage, LabelStats } from './types';
import { InterrogationChat } from './InterrogationChat';
import './DecisionAnalyzer.css';

interface DecisionAnalyzerProps {
  onBack?: () => void;
  embedded?: boolean;
  /** Called when detail mode changes (for parent to adjust back button behavior) */
  onDetailModeChange?: (inDetailMode: boolean, backToList: () => void) => void;
  /** Initial capture ID to load (from URL) */
  initialCaptureId?: number;
  /** Called when a capture is selected (for URL updates) */
  onCaptureSelect?: (captureId: number | null) => void;
}

export function DecisionAnalyzer({ onBack, embedded = false, onDetailModeChange, initialCaptureId, onCaptureSelect }: DecisionAnalyzerProps) {
  // Viewport detection for responsive layout
  const { isMobile } = useViewport();

  // Mobile panel switching state - when true, show detail panel on mobile
  const [showMobileDetail, setShowMobileDetail] = useState(false);

  // Notify parent when detail mode changes so it can adjust back button behavior
  useEffect(() => {
    if (isMobile && onDetailModeChange) {
      onDetailModeChange(showMobileDetail, () => setShowMobileDetail(false));
    }
  }, [showMobileDetail, isMobile, onDetailModeChange]);

  // Mobile filter sheet state
  const [filterSheetOpen, setFilterSheetOpen] = useState(false);

  const [captures, setCaptures] = useState<PromptCapture[]>([]);
  const [stats, setStats] = useState<CaptureStats | null>(null);
  const [labelStats, setLabelStats] = useState<LabelStats | null>(null);
  const [analysisStats, setAnalysisStats] = useState<DecisionAnalysisStats | null>(null);
  const [selectedCapture, setSelectedCapture] = useState<PromptCapture | null>(null);
  const [selectedAnalysis, setSelectedAnalysis] = useState<DecisionAnalysis | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [total, setTotal] = useState(0);
  const [availableEmotions, setAvailableEmotions] = useState<string[]>([]);

  // Filters
  const [filters, setFilters] = useState<CaptureFilters>({
    limit: 50,
    offset: 0,
  });

  // Mode state (view, replay, interrogate)
  const [mode, setMode] = useState<DebugMode>('view');

  // Replay state
  const [modifiedSystemPrompt, setModifiedSystemPrompt] = useState('');
  const [modifiedUserMessage, setModifiedUserMessage] = useState('');
  const [modifiedConversationHistory, setModifiedConversationHistory] = useState<ConversationMessage[]>([]);
  const [useHistory, setUseHistory] = useState(true);
  const [replayResult, setReplayResult] = useState<ReplayResponse | null>(null);
  const [replaying, setReplaying] = useState(false);
  const [replayProvider, setReplayProvider] = useState('openai');
  const [replayModel, setReplayModel] = useState('gpt-5-nano');
  const [replayReasoningEffort, setReplayReasoningEffort] = useState('minimal');

  // Interrogation state
  const [interrogationMessages, setInterrogationMessages] = useState<InterrogationMessage[]>([]);
  const [interrogationSessionId, setInterrogationSessionId] = useState<string | null>(null);
  const [interrogateProvider, setInterrogateProvider] = useState('openai');
  const [interrogateModel, setInterrogateModel] = useState('gpt-5-nano');
  const [interrogateReasoningEffort, setInterrogateReasoningEffort] = useState('minimal');

  // Provider and model configuration (fetched from API)
  // Using 'system' scope to include system-only models for admin tools
  const {
    providers,
    getModelsForProvider,
  } = useLLMProviders({ scope: 'system' });
  const reasoningLevels = ['minimal', 'low', 'medium', 'high'];

  // Build the raw request messages array
  const buildRawRequest = useCallback((capture: PromptCapture) => {
    const messages: Array<{ role: string; content: string }> = [];

    // System prompt
    if (capture.system_prompt) {
      messages.push({ role: 'system', content: capture.system_prompt });
    }

    // Conversation history (prior turns only - current turn is stored separately)
    if (capture.conversation_history) {
      for (const msg of capture.conversation_history) {
        messages.push({ role: msg.role, content: msg.content });
      }
    }

    // Current user message
    if (capture.user_message) {
      messages.push({ role: 'user', content: capture.user_message });
    }

    return {
      model: capture.model,
      messages,
      // Include other request params if available
      ...(capture.reasoning_effort && { reasoning_effort: capture.reasoning_effort }),
    };
  }, []);

  // Download JSON file helper
  const downloadJson = useCallback((data: unknown, filename: string) => {
    const json = JSON.stringify(data, null, 2);
    const blob = new Blob([json], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }, []);

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
      if (filters.min_pot_size !== undefined) params.set('min_pot_size', filters.min_pot_size.toString());
      if (filters.max_pot_size !== undefined) params.set('max_pot_size', filters.max_pot_size.toString());
      if (filters.min_big_blind !== undefined) params.set('min_big_blind', filters.min_big_blind.toString());
      if (filters.max_big_blind !== undefined) params.set('max_big_blind', filters.max_big_blind.toString());
      if (filters.labels && filters.labels.length > 0) params.set('labels', filters.labels.join(','));
      if (filters.labelMatchAll) params.set('label_match_all', 'true');
      if (filters.error_type) params.set('error_type', filters.error_type);
      if (filters.has_error !== undefined) params.set('has_error', filters.has_error.toString());
      if (filters.is_correction !== undefined) params.set('is_correction', filters.is_correction.toString());
      if (filters.display_emotion) params.set('display_emotion', filters.display_emotion);
      if (filters.min_tilt_level !== undefined) params.set('min_tilt_level', filters.min_tilt_level.toString());
      if (filters.max_tilt_level !== undefined) params.set('max_tilt_level', filters.max_tilt_level.toString());
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
      setCaptures(data.captures || []);
      setStats(data.stats);
      setLabelStats(data.label_stats || null);
      setTotal(data.total || 0);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unknown error');
    } finally {
      setLoading(false);
    }
  }, [filters]);

  const fetchAnalysisStats = useCallback(async () => {
    try {
      const params = new URLSearchParams();
      if (filters.game_id) params.set('game_id', filters.game_id);

      const response = await fetch(
        `${config.API_URL}/api/prompt-debug/analysis-stats?${params}`,
        { credentials: 'include' }
      );

      if (response.ok) {
        const data = await response.json();
        setAnalysisStats(data.stats);
      }
    } catch (err) {
      // Silently ignore - analysis stats are optional
      logger.debug('Failed to fetch analysis stats:', err);
    }
  }, [filters.game_id]);

  // Fetch distinct emotions for filter dropdown (once on mount)
  useEffect(() => {
    (async () => {
      try {
        const response = await fetch(
          `${config.API_URL}/api/prompt-debug/emotions`,
          { credentials: 'include' }
        );
        if (response.ok) {
          const data = await response.json();
          setAvailableEmotions(data.emotions || []);
        }
      } catch (err) {
        logger.debug('Failed to fetch emotions:', err);
      }
    })();
  }, []);

  useEffect(() => {
    fetchCaptures();
    fetchAnalysisStats();
  }, [fetchCaptures, fetchAnalysisStats]);

  // Get models for a specific provider (with fallback)
  const getModelsForProviderWithFallback = useCallback((providerId: string): string[] => {
    const models = getModelsForProvider(providerId);
    return models.length > 0 ? models : ['gpt-5-nano', 'gpt-5-mini', 'gpt-5'];
  }, [getModelsForProvider]);

  // Handle provider change for replay
  const handleReplayProviderChange = useCallback((newProvider: string) => {
    setReplayProvider(newProvider);
    const models = getModelsForProviderWithFallback(newProvider);
    if (models.length > 0 && !models.includes(replayModel)) {
      setReplayModel(models[0]);
    }
  }, [getModelsForProviderWithFallback, replayModel]);

  // Handle provider change for interrogate
  const handleInterrogateProviderChange = useCallback((newProvider: string) => {
    setInterrogateProvider(newProvider);
    const models = getModelsForProviderWithFallback(newProvider);
    if (models.length > 0 && !models.includes(interrogateModel)) {
      setInterrogateModel(models[0]);
    }
  }, [getModelsForProviderWithFallback, interrogateModel]);

  const fetchCaptureDetail = async (captureId: number, updateUrl = true) => {
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
      setSelectedAnalysis(data.decision_analysis || null);
      setModifiedSystemPrompt(data.capture.system_prompt);
      setModifiedUserMessage(data.capture.user_message);
      setModifiedConversationHistory(data.capture.conversation_history || []);
      setUseHistory(true);
      setMode('view');
      setReplayResult(null);
      // On mobile, switch to detail panel view
      setShowMobileDetail(true);
      // Set initial provider/model/reasoning from capture (use original values)
      setReplayProvider(data.capture.provider || 'openai');
      setReplayModel(data.capture.model || 'gpt-5-nano');
      setReplayReasoningEffort(data.capture.reasoning_effort || 'minimal');
      // Reset interrogation state for new capture
      setInterrogationMessages([]);
      setInterrogationSessionId(null);
      setInterrogateProvider(data.capture.provider || 'openai');
      setInterrogateModel(data.capture.model || 'gpt-5-nano');
      setInterrogateReasoningEffort(data.capture.reasoning_effort || 'minimal');
      // Notify parent to update URL
      if (updateUrl) {
        onCaptureSelect?.(captureId);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unknown error');
    }
  };

  // Load initial capture from URL if provided
  // Note: selectedCapture is intentionally excluded from deps to prevent re-fetching
  // when user navigates away and back. We only want to load once per initialCaptureId.
  useEffect(() => {
    if (initialCaptureId && !selectedCapture) {
      fetchCaptureDetail(initialCaptureId, false);
    }
  }, [initialCaptureId]); // eslint-disable-line react-hooks/exhaustive-deps

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
            conversation_history: modifiedConversationHistory,
            use_history: useHistory,
            provider: replayProvider,
            model: replayModel,
            reasoning_effort: replayReasoningEffort,
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

  const updateHistoryMessage = (index: number, field: 'role' | 'content', value: string) => {
    setModifiedConversationHistory(prev => {
      const updated = [...prev];
      updated[index] = { ...updated[index], [field]: value as ConversationMessage['role'] };
      return updated;
    });
  };

  const removeHistoryMessage = (index: number) => {
    setModifiedConversationHistory(prev => prev.filter((_, i) => i !== index));
  };

  const addHistoryMessage = () => {
    setModifiedConversationHistory(prev => [...prev, { role: 'user', content: '' }]);
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

  // Count active filters for mobile filter button badge
  const getActiveFilterCount = () => {
    let count = 0;
    if (filters.action) count++;
    if (filters.phase) count++;
    if (filters.min_pot_odds !== undefined) count++;
    if (filters.labels && filters.labels.length > 0) count += filters.labels.length;
    if (filters.error_type) count++;
    if (filters.has_error !== undefined) count++;
    if (filters.is_correction !== undefined) count++;
    if (filters.display_emotion) count++;
    if (filters.min_tilt_level !== undefined) count++;
    if (filters.max_tilt_level !== undefined) count++;
    return count;
  };

  // Toggle a label in the filter
  const toggleLabelFilter = (label: string) => {
    const currentLabels = filters.labels || [];
    const newLabels = currentLabels.includes(label)
      ? currentLabels.filter(l => l !== label)
      : [...currentLabels, label];
    setFilters({ ...filters, labels: newLabels.length > 0 ? newLabels : undefined, offset: 0 });
  };

  // Format label name for display
  const formatLabelName = (label: string) => {
    return label.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
  };

  // Get severity class for label
  const getLabelSeverity = (label: string): 'high' | 'medium' | 'low' => {
    const highSeverity = ['fold_mistake', 'high_ev_loss', 'short_stack_fold', 'pot_committed_fold'];
    const mediumSeverity = ['bad_all_in', 'suspicious_fold'];
    if (highSeverity.includes(label)) return 'high';
    if (mediumSeverity.includes(label)) return 'medium';
    return 'low';
  };

  // Format emotion name for display
  const formatEmotionName = (emotion: string): string =>
    emotion.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());

  // Default filter state
  const DEFAULT_FILTERS: CaptureFilters = { limit: 50, offset: 0, labels: undefined, error_type: undefined, has_error: undefined, is_correction: undefined, display_emotion: undefined, min_tilt_level: undefined, max_tilt_level: undefined };

  // Get tilt bar color class
  const getTiltBarClass = (tiltLevel: number): string => {
    if (tiltLevel < 0.3) return 'tilt-bar-fill--low';
    if (tiltLevel < 0.6) return 'tilt-bar-fill--medium';
    return 'tilt-bar-fill--high';
  };

  // Render psychology section (shared between mobile and desktop)
  const renderPsychologySection = (analysis: DecisionAnalysis) => {
    const hasPsychology = analysis.display_emotion != null || analysis.tilt_level != null ||
      analysis.valence != null || analysis.arousal != null || analysis.control != null ||
      analysis.focus != null || analysis.elastic_aggression != null || analysis.elastic_bluff_tendency != null;

    if (!hasPsychology) return null;

    return (
      <div className="psychology-section">
        <h4>Psychology</h4>
        <div className="psychology-grid">
          {analysis.display_emotion != null && (
            <div className="psychology-item">
              <label>Emotion:</label>
              <span className={`emotion-badge emotion-badge--${analysis.display_emotion}`}>
                {analysis.display_emotion}
              </span>
            </div>
          )}
          {analysis.tilt_level != null && (
            <div className="psychology-item">
              <label>Tilt:</label>
              <div className="tilt-bar">
                <span>{(analysis.tilt_level * 100).toFixed(0)}%</span>
                <div className="tilt-bar-track">
                  <div
                    className={`tilt-bar-fill ${getTiltBarClass(analysis.tilt_level)}`}
                    style={{ width: `${analysis.tilt_level * 100}%` }}
                  />
                </div>
              </div>
              {analysis.tilt_source && (
                <span style={{ fontSize: '0.7rem', color: 'rgba(255,255,255,0.5)' }}>
                  {analysis.tilt_source}
                </span>
              )}
            </div>
          )}
          {analysis.valence != null && (
            <div className="psychology-item">
              <label>Valence:</label>
              <span>{analysis.valence.toFixed(2)}</span>
            </div>
          )}
          {analysis.arousal != null && (
            <div className="psychology-item">
              <label>Arousal:</label>
              <span>{analysis.arousal.toFixed(2)}</span>
            </div>
          )}
          {analysis.control != null && (
            <div className="psychology-item">
              <label>Control:</label>
              <span>{analysis.control.toFixed(2)}</span>
            </div>
          )}
          {analysis.focus != null && (
            <div className="psychology-item">
              <label>Focus:</label>
              <span>{analysis.focus.toFixed(2)}</span>
            </div>
          )}
          {analysis.elastic_aggression != null && (
            <div className="psychology-item">
              <label>Aggression:</label>
              <span>{analysis.elastic_aggression.toFixed(2)}</span>
            </div>
          )}
          {analysis.elastic_bluff_tendency != null && (
            <div className="psychology-item">
              <label>Bluff Tendency:</label>
              <span>{analysis.elastic_bluff_tendency.toFixed(2)}</span>
            </div>
          )}
        </div>
      </div>
    );
  };

  // Render prompt config section
  const renderPromptConfigSection = (capture: PromptCapture) => {
    if (!capture.prompt_config_json) return null;

    let configDisplay: string;
    try {
      const parsed = JSON.parse(capture.prompt_config_json);
      configDisplay = JSON.stringify(parsed, null, 2);
    } catch {
      configDisplay = capture.prompt_config_json;
    }

    return (
      <details className="prompt-config-section">
        <summary>Prompt Config</summary>
        <pre>{configDisplay}</pre>
      </details>
    );
  };

  const activeFilterCount = getActiveFilterCount();

  // Get current capture index in filtered list for prev/next navigation
  const currentCaptureIndex = selectedCapture
    ? captures.findIndex(c => c.id === selectedCapture.id)
    : -1;

  const hasPrevCapture = currentCaptureIndex > 0;
  const hasNextCapture = currentCaptureIndex >= 0 && currentCaptureIndex < captures.length - 1;

  const goToPrevCapture = () => {
    if (hasPrevCapture) {
      fetchCaptureDetail(captures[currentCaptureIndex - 1].id);
    }
  };

  const goToNextCapture = () => {
    if (hasNextCapture) {
      fetchCaptureDetail(captures[currentCaptureIndex + 1].id);
    }
  };

  // List panel component for reuse
  const listPanel = (
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
          {/* Display error info */}
          {capture.error_type && (
            <div className="capture-error" title={capture.error_description || undefined}>
              <span className="error-badge">{capture.error_type.replace(/_/g, ' ')}</span>
              {(capture.correction_attempt ?? 0) > 0 && (
                <span className="correction-badge">Attempt #{capture.correction_attempt}</span>
              )}
              {capture.error_description && (
                <span className="error-description">{capture.error_description}</span>
              )}
            </div>
          )}
          {/* Display labels for this capture */}
          {capture.labels && capture.labels.length > 0 && (
            <div className="capture-labels">
              {capture.labels.map(({ label }) => (
                <span key={label} className={`capture-label capture-label--${getLabelSeverity(label)}`}>
                  {formatLabelName(label)}
                </span>
              ))}
            </div>
          )}
          {isSuspiciousFold(capture) && !capture.labels?.some(l => l.label === 'suspicious_fold') && (
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
  );

  const content = (
    <div className={`decision-analyzer${embedded ? ' decision-analyzer--embedded' : ''}${isMobile ? ' decision-analyzer--mobile' : ''}${showMobileDetail ? ' decision-analyzer--showing-detail' : ''}`}>
      {/* Mobile detail header bar - shows player info and prev/next when viewing detail */}
      {isMobile && showMobileDetail && selectedCapture && (
        <div className="decision-analyzer__detail-bar">
          <div className="decision-analyzer__detail-info">
            <span className="decision-analyzer__detail-player">{selectedCapture.player_name}</span>
            <span className={`decision-analyzer__detail-action ${getActionColor(selectedCapture.action_taken)}`}>
              {selectedCapture.action_taken?.toUpperCase()}
            </span>
          </div>
          <div className="decision-analyzer__detail-nav">
            <button
              className="decision-analyzer__nav-btn"
              onClick={goToPrevCapture}
              disabled={!hasPrevCapture}
              type="button"
              aria-label="Previous"
            >
              <ChevronLeft size={20} />
            </button>
            <span className="decision-analyzer__nav-count">{currentCaptureIndex + 1}/{captures.length}</span>
            <button
              className="decision-analyzer__nav-btn"
              onClick={goToNextCapture}
              disabled={!hasNextCapture}
              type="button"
              aria-label="Next"
            >
              <ChevronRight size={20} />
            </button>
          </div>
        </div>
      )}

      {/* Mobile list header bar - shows filter and refresh icons */}
      {isMobile && !showMobileDetail && (
        <MobileFilterBar
          activeFilterCount={activeFilterCount}
          onFilterClick={() => setFilterSheetOpen(true)}
          actions={
            <button
              className="mobile-filter-bar__icon-btn"
              onClick={fetchCaptures}
              disabled={loading}
              type="button"
              aria-label="Refresh"
            >
              <RefreshCw size={20} className={loading ? 'spinning' : ''} />
            </button>
          }
        />
      )}

      {/* Decision Analysis Stats - hidden on mobile when showing detail */}
      {analysisStats && analysisStats.total > 0 && (!isMobile || !showMobileDetail) && (
        <div className="debugger-stats analysis-stats">
          <div className="stat-item">
            <span className="stat-value">{analysisStats.total}</span>
            <span className="stat-label">Analyzed</span>
          </div>
          <div className="stat-item stat-success">
            <span className="stat-value">{analysisStats.correct}</span>
            <span className="stat-label">Correct</span>
          </div>
          <div className="stat-item stat-danger">
            <span className="stat-value">{analysisStats.mistakes}</span>
            <span className="stat-label">Mistakes</span>
          </div>
          <div className="stat-item">
            <span className="stat-value">${Math.round(analysisStats.total_ev_lost)}</span>
            <span className="stat-label">EV Lost</span>
          </div>
          {analysisStats.avg_equity !== null && (
            <div className="stat-item">
              <span className="stat-value">{(analysisStats.avg_equity * 100).toFixed(1)}%</span>
              <span className="stat-label">Avg Equity</span>
            </div>
          )}
          {analysisStats.avg_equity_vs_ranges !== null && (
            <div className="stat-item">
              <span className="stat-value">{(analysisStats.avg_equity_vs_ranges * 100).toFixed(1)}%</span>
              <span className="stat-label">Equity (Ranges)</span>
            </div>
          )}
          {/* Selected action counts row */}
          {stats && (
            <div className="stat-row">
              <div className="stat-item stat-warning">
                <span className="stat-value">{stats.suspicious_folds}</span>
                <span className="stat-label">Sus Folds</span>
              </div>
              <div className="stat-item action-allin">
                <span className="stat-value">{stats.by_action.all_in || 0}</span>
                <span className="stat-label">All In</span>
              </div>
              <div className="stat-item action-call">
                <span className="stat-value">{stats.by_action.call || 0}</span>
                <span className="stat-label">Call</span>
              </div>
              <div className="stat-item action-raise">
                <span className="stat-value">{stats.by_action.raise || 0}</span>
                <span className="stat-label">Raise</span>
              </div>
            </div>
          )}
          {/* Label stats row - clickable chips */}
          {labelStats && Object.keys(labelStats).length > 0 && (
            <div className="stat-row label-stats-row">
              {Object.entries(labelStats)
                .filter(([, count]) => count > 0)
                .map(([label, count]) => (
                  <button
                    key={label}
                    className={`label-chip label-chip--${getLabelSeverity(label)} ${filters.labels?.includes(label) ? 'label-chip--selected' : ''}`}
                    onClick={() => toggleLabelFilter(label)}
                    type="button"
                  >
                    <span className="label-chip__count">{count}</span>
                    <span className="label-chip__name">{formatLabelName(label)}</span>
                  </button>
                ))}
            </div>
          )}
        </div>
      )}

      {/* Filters - desktop only (mobile filters are in header) */}
      {!isMobile && (
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

            <select
              value={filters.error_type || ''}
              onChange={(e) => setFilters({ ...filters, error_type: e.target.value || undefined, offset: 0 })}
            >
              <option value="">All Error Types</option>
              <option value="malformed_json">Malformed JSON</option>
              <option value="missing_field">Missing Field</option>
              <option value="invalid_action">Invalid Action</option>
              <option value="semantic_error">Semantic Error</option>
            </select>

            <select
              value={filters.has_error === undefined ? '' : filters.has_error.toString()}
              onChange={(e) => setFilters({
                ...filters,
                has_error: e.target.value === '' ? undefined : e.target.value === 'true',
                offset: 0
              })}
            >
              <option value="">All (Errors)</option>
              <option value="true">Has Error</option>
              <option value="false">No Error</option>
            </select>

            <select
              value={filters.is_correction === undefined ? '' : filters.is_correction.toString()}
              onChange={(e) => setFilters({
                ...filters,
                is_correction: e.target.value === '' ? undefined : e.target.value === 'true',
                offset: 0
              })}
            >
              <option value="">All (Corrections)</option>
              <option value="false">Original Only</option>
              <option value="true">Corrections Only</option>
            </select>

            <select
              value={filters.display_emotion || ''}
              onChange={(e) => setFilters({
                ...filters,
                display_emotion: e.target.value || undefined,
                offset: 0
              })}
            >
              <option value="">All (Emotion)</option>
              {availableEmotions.map(e => (
                <option key={e} value={e}>{formatEmotionName(e)}</option>
              ))}
            </select>

            <input
              type="number"
              placeholder="Min Tilt"
              value={filters.min_tilt_level ?? ''}
              onChange={(e) => setFilters({
                ...filters,
                min_tilt_level: e.target.value ? parseFloat(e.target.value) : undefined,
                offset: 0
              })}
              min={0}
              max={1}
              step={0.1}
              style={{ width: '90px' }}
            />

            <input
              type="number"
              placeholder="Max Tilt"
              value={filters.max_tilt_level ?? ''}
              onChange={(e) => setFilters({
                ...filters,
                max_tilt_level: e.target.value ? parseFloat(e.target.value) : undefined,
                offset: 0
              })}
              min={0}
              max={1}
              step={0.1}
              style={{ width: '90px' }}
            />

            {/* Label filter chips - desktop */}
            {labelStats && Object.keys(labelStats).length > 0 && (
              <div className="debugger-filter-chips debugger-filter-chips--inline">
                {Object.entries(labelStats)
                  .filter(([, count]) => count > 0)
                  .map(([label, count]) => (
                    <button
                      key={label}
                      className={`label-chip label-chip--small label-chip--${getLabelSeverity(label)} ${filters.labels?.includes(label) ? 'label-chip--selected' : ''}`}
                      onClick={() => toggleLabelFilter(label)}
                      type="button"
                    >
                      <span className="label-chip__count">{count}</span>
                      <span className="label-chip__name">{formatLabelName(label)}</span>
                    </button>
                  ))}
              </div>
            )}

            <button onClick={() => setFilters(DEFAULT_FILTERS)}>
              Clear Filters
            </button>

            <button
              className="debugger-refresh-btn debugger-refresh-btn--desktop"
              onClick={fetchCaptures}
              disabled={loading}
              type="button"
              aria-label="Refresh"
            >
              <RefreshCw size={16} className={loading ? 'spinning' : ''} />
            </button>
          </div>
      )}

      {error && <div className="debugger-error">{error}</div>}

      {/* Mobile: Show list OR detail based on showMobileDetail state */}
      {isMobile ? (
        showMobileDetail ? (
          // Mobile Detail View
          <div className="capture-detail capture-detail--mobile-fullwidth">
            {selectedCapture ? (
              <>
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

                {/* Error/Correction Info */}
                {(selectedCapture.error_type || selectedCapture.parent_id) && (
                  <div className="error-info-panel">
                    {selectedCapture.error_type && (
                      <div className="error-info-item">
                        <label>Error Type:</label>
                        <span className="error-type-value">{selectedCapture.error_type.replace(/_/g, ' ')}</span>
                      </div>
                    )}
                    {selectedCapture.error_description && (
                      <div className="error-info-item error-info-item--full">
                        <label>Error:</label>
                        <span>{selectedCapture.error_description}</span>
                      </div>
                    )}
                    {selectedCapture.parent_id && (
                      <div className="error-info-item">
                        <label>Parent Capture:</label>
                        <button
                          type="button"
                          className="link-button"
                          onClick={() => fetchCaptureDetail(selectedCapture.parent_id!)}
                        >
                          #{selectedCapture.parent_id}
                        </button>
                      </div>
                    )}
                    {selectedCapture.correction_attempt != null && selectedCapture.correction_attempt > 0 && (
                      <div className="error-info-item">
                        <label>Correction Attempt:</label>
                        <span>#{selectedCapture.correction_attempt}</span>
                      </div>
                    )}
                  </div>
                )}

                {/* Decision Analysis */}
                {selectedAnalysis && (
                  <div className={`decision-analysis ${selectedAnalysis.decision_quality === 'mistake' ? 'mistake' : selectedAnalysis.decision_quality === 'correct' ? 'correct' : ''}`}>
                    <h4>Decision Analysis</h4>
                    <div className="analysis-grid">
                      {selectedAnalysis.equity != null && (
                        <div className="analysis-item">
                          <label>Equity:</label>
                          <span>{(selectedAnalysis.equity * 100).toFixed(1)}%</span>
                        </div>
                      )}
                      {selectedAnalysis.equity_vs_ranges != null && (
                        <div className="analysis-item">
                          <label>Equity vs Ranges:</label>
                          <span>
                            {(selectedAnalysis.equity_vs_ranges * 100).toFixed(1)}%
                            {selectedAnalysis.opponent_positions && (
                              <span className="opponent-positions">
                                {' '}(vs {JSON.parse(selectedAnalysis.opponent_positions).join(', ')})
                              </span>
                            )}
                          </span>
                        </div>
                      )}
                      {selectedAnalysis.required_equity != null && (
                        <div className="analysis-item">
                          <label>Required Equity:</label>
                          <span>{(selectedAnalysis.required_equity * 100).toFixed(1)}%</span>
                        </div>
                      )}
                      {selectedAnalysis.ev_call != null && (
                        <div className="analysis-item">
                          <label>EV (Call):</label>
                          <span className={selectedAnalysis.ev_call >= 0 ? 'positive' : 'negative'}>
                            {selectedAnalysis.ev_call >= 0 ? '+' : ''}${selectedAnalysis.ev_call.toFixed(0)}
                          </span>
                        </div>
                      )}
                      {selectedAnalysis.optimal_action && (
                        <div className="analysis-item">
                          <label>Optimal Action:</label>
                          <span className={`optimal-action ${selectedAnalysis.optimal_action}`}>
                            {selectedAnalysis.optimal_action.toUpperCase()}
                          </span>
                        </div>
                      )}
                      {selectedAnalysis.decision_quality && (
                        <div className="analysis-item quality">
                          <label>Quality:</label>
                          <span className={`quality-badge ${selectedAnalysis.decision_quality}`}>
                            {selectedAnalysis.decision_quality.toUpperCase()}
                          </span>
                        </div>
                      )}
                      {selectedAnalysis.ev_lost != null && selectedAnalysis.ev_lost > 0 && (
                        <div className="analysis-item">
                          <label>EV Lost:</label>
                          <span className="negative">-${selectedAnalysis.ev_lost.toFixed(0)}</span>
                        </div>
                      )}
                    </div>
                  </div>
                )}

                {/* Psychology */}
                {selectedAnalysis && renderPsychologySection(selectedAnalysis)}

                {/* Prompt Config */}
                {selectedCapture && renderPromptConfigSection(selectedCapture)}

                <div className="detail-tabs">
                  <button
                    className={mode === 'view' ? 'active' : ''}
                    onClick={() => setMode('view')}
                  >
                    View
                  </button>
                  <button
                    className={mode === 'replay' ? 'active' : ''}
                    onClick={() => setMode('replay')}
                  >
                    Edit & Replay
                  </button>
                  <button
                    className={mode === 'interrogate' ? 'active' : ''}
                    onClick={() => {
                      setMode('interrogate');
                      if (selectedCapture && interrogationMessages.length === 0) {
                        setInterrogationMessages([{
                          id: 'original-decision',
                          role: 'context',
                          content: selectedCapture.ai_response,
                          timestamp: selectedCapture.created_at,
                        }]);
                      }
                    }}
                  >
                    Interrogate
                  </button>
                </div>

                {/* Token & Latency Info */}
                {(selectedCapture.input_tokens || selectedCapture.latency_ms) && (
                  <div className="token-info">
                    <div className="token-info-row">
                      {(selectedCapture.provider || selectedCapture.model) && (
                        <span>
                          {selectedCapture.provider && <strong>{selectedCapture.provider}</strong>}
                          {selectedCapture.provider && selectedCapture.model && ' / '}
                          {selectedCapture.model}
                          {selectedCapture.reasoning_effort && ` (${selectedCapture.reasoning_effort})`}
                        </span>
                      )}
                      {selectedCapture.latency_ms && <span>Latency: {selectedCapture.latency_ms.toLocaleString()}ms</span>}
                      {selectedCapture.estimated_cost != null && (
                        <span className="cost">Cost: ${selectedCapture.estimated_cost.toFixed(4)}</span>
                      )}
                    </div>
                    <div className="token-info-row">
                      <span className="token-count cached">Cached: {(selectedCapture.cached_tokens ?? 0).toLocaleString()}</span>
                      <span className="token-count input">Input: {((selectedCapture.input_tokens ?? 0) - (selectedCapture.cached_tokens ?? 0)).toLocaleString()}</span>
                      <span className="token-count total-in">Total In: {(selectedCapture.input_tokens ?? 0).toLocaleString()}</span>
                    </div>
                    <div className="token-info-row">
                      <span className="token-count reasoning">Reasoning: {(selectedCapture.reasoning_tokens ?? 0).toLocaleString()}</span>
                      <span className="token-count output">Output: {(selectedCapture.output_tokens ?? 0).toLocaleString()}</span>
                      <span className="token-count total-out">Total Out: {((selectedCapture.reasoning_tokens ?? 0) + (selectedCapture.output_tokens ?? 0)).toLocaleString()}</span>
                    </div>
                  </div>
                )}

                {mode === 'view' && (
                  <div className="detail-prompts">
                    <div className="prompt-section">
                      <h4>System Prompt</h4>
                      <pre>{selectedCapture.system_prompt}</pre>
                    </div>

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

                    <div className="download-buttons">
                      <button
                        className="download-button"
                        onClick={() => {
                          const request = buildRawRequest(selectedCapture);
                          const filename = `request_${selectedCapture.id}_${selectedCapture.player_name}_h${selectedCapture.hand_number || 0}.json`;
                          downloadJson(request, filename);
                        }}
                      >
                        Download Request
                      </button>
                      {selectedCapture.raw_api_response && (
                        <button
                          className="download-button"
                          onClick={() => {
                            const response = JSON.parse(selectedCapture.raw_api_response!);
                            const filename = `response_${selectedCapture.id}_${selectedCapture.player_name}_h${selectedCapture.hand_number || 0}.json`;
                            downloadJson(response, filename);
                          }}
                        >
                          Download Response
                        </button>
                      )}
                    </div>

                    {selectedCapture.raw_api_response && (
                      <details className="prompt-section raw-response">
                        <summary>
                          <h4>Raw API Response (click to expand)</h4>
                        </summary>
                        <pre>{JSON.stringify(JSON.parse(selectedCapture.raw_api_response), null, 2)}</pre>
                      </details>
                    )}
                  </div>
                )}

                {mode === 'replay' && (
                  <div className="replay-editor">
                    <div className="prompt-section">
                      <h4>System Prompt (editable)</h4>
                      <textarea
                        value={modifiedSystemPrompt}
                        onChange={(e) => setModifiedSystemPrompt(e.target.value)}
                        rows={10}
                      />
                    </div>

                    <div className="prompt-section conversation-history-editor">
                      <div className="history-header">
                        <h4>Conversation History ({modifiedConversationHistory.length} messages)</h4>
                        <label className="history-toggle">
                          <input
                            type="checkbox"
                            checked={useHistory}
                            onChange={(e) => setUseHistory(e.target.checked)}
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
                                onChange={(e) => updateHistoryMessage(idx, 'role', e.target.value)}
                              >
                                <option value="user">user</option>
                                <option value="assistant">assistant</option>
                                <option value="system">system</option>
                              </select>
                              <textarea
                                value={msg.content}
                                onChange={(e) => updateHistoryMessage(idx, 'content', e.target.value)}
                                rows={3}
                                placeholder="Message content..."
                              />
                              <button
                                className="remove-message"
                                onClick={() => removeHistoryMessage(idx)}
                                title="Remove message"
                              >
                                ×
                              </button>
                            </div>
                          ))}
                          <button className="add-message" onClick={addHistoryMessage}>
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
                        onChange={(e) => setModifiedUserMessage(e.target.value)}
                        rows={15}
                      />
                    </div>

                    <div className="replay-settings">
                      <div className="setting-group">
                        <label>Provider:</label>
                        <select
                          value={replayProvider}
                          onChange={(e) => handleReplayProviderChange(e.target.value)}
                        >
                          {providers.length > 0 ? (
                            providers.map(p => (
                              <option key={p.id} value={p.id}>{p.name}</option>
                            ))
                          ) : (
                            <option value="openai">OpenAI</option>
                          )}
                        </select>
                      </div>
                      <div className="setting-group">
                        <label>Model:</label>
                        <select
                          value={replayModel}
                          onChange={(e) => setReplayModel(e.target.value)}
                        >
                          {getModelsForProviderWithFallback(replayProvider).map(model => (
                            <option key={model} value={model}>{model}</option>
                          ))}
                        </select>
                      </div>
                      <div className="setting-group">
                        <label>Reasoning:</label>
                        <select
                          value={replayReasoningEffort}
                          onChange={(e) => setReplayReasoningEffort(e.target.value)}
                        >
                          {reasoningLevels.map(level => (
                            <option key={level} value={level}>{level}</option>
                          ))}
                        </select>
                      </div>
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
                )}

                {mode === 'interrogate' && (
                  <InterrogationChat
                    capture={selectedCapture}
                    messages={interrogationMessages}
                    onMessagesUpdate={setInterrogationMessages}
                    sessionId={interrogationSessionId}
                    onSessionIdUpdate={setInterrogationSessionId}
                    provider={interrogateProvider}
                    onProviderChange={handleInterrogateProviderChange}
                    model={interrogateModel}
                    onModelChange={setInterrogateModel}
                    reasoningEffort={interrogateReasoningEffort}
                    onReasoningEffortChange={setInterrogateReasoningEffort}
                    providers={providers}
                    getModelsForProvider={getModelsForProviderWithFallback}
                    reasoningLevels={reasoningLevels}
                  />
                )}
              </>
            ) : (
              <div className="no-selection">
                Select a capture from the list to view details
              </div>
            )}
          </div>
        ) : (
          // Mobile List View
          listPanel
        )
      ) : (
        // Desktop: Side-by-side layout
        <div className="debugger-layout">
          {/* Capture List */}
          {listPanel}

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

              {/* Error/Correction Info */}
              {(selectedCapture.error_type || selectedCapture.parent_id) && (
                <div className="error-info-panel">
                  {selectedCapture.error_type && (
                    <div className="error-info-item">
                      <label>Error Type:</label>
                      <span className="error-type-value">{selectedCapture.error_type.replace(/_/g, ' ')}</span>
                    </div>
                  )}
                  {selectedCapture.error_description && (
                    <div className="error-info-item error-info-item--full">
                      <label>Error:</label>
                      <span>{selectedCapture.error_description}</span>
                    </div>
                  )}
                  {selectedCapture.parent_id && (
                    <div className="error-info-item">
                      <label>Parent Capture:</label>
                      <button
                        type="button"
                        className="link-button"
                        onClick={() => fetchCaptureDetail(selectedCapture.parent_id!)}
                      >
                        #{selectedCapture.parent_id}
                      </button>
                    </div>
                  )}
                  {selectedCapture.correction_attempt != null && selectedCapture.correction_attempt > 0 && (
                    <div className="error-info-item">
                      <label>Correction Attempt:</label>
                      <span>#{selectedCapture.correction_attempt}</span>
                    </div>
                  )}
                </div>
              )}

              {/* Decision Analysis */}
              {selectedAnalysis && (
                <div className={`decision-analysis ${selectedAnalysis.decision_quality === 'mistake' ? 'mistake' : selectedAnalysis.decision_quality === 'correct' ? 'correct' : ''}`}>
                  <h4>Decision Analysis</h4>
                  <div className="analysis-grid">
                    {selectedAnalysis.equity != null && (
                      <div className="analysis-item">
                        <label>Equity:</label>
                        <span>{(selectedAnalysis.equity * 100).toFixed(1)}%</span>
                      </div>
                    )}
                    {selectedAnalysis.equity_vs_ranges != null && (
                      <div className="analysis-item">
                        <label>Equity vs Ranges:</label>
                        <span>
                          {(selectedAnalysis.equity_vs_ranges * 100).toFixed(1)}%
                          {selectedAnalysis.opponent_positions && (
                            <span className="opponent-positions">
                              {' '}(vs {JSON.parse(selectedAnalysis.opponent_positions).join(', ')})
                            </span>
                          )}
                        </span>
                      </div>
                    )}
                    {selectedAnalysis.required_equity != null && (
                      <div className="analysis-item">
                        <label>Required Equity:</label>
                        <span>{(selectedAnalysis.required_equity * 100).toFixed(1)}%</span>
                      </div>
                    )}
                    {selectedAnalysis.ev_call != null && (
                      <div className="analysis-item">
                        <label>EV (Call):</label>
                        <span className={selectedAnalysis.ev_call >= 0 ? 'positive' : 'negative'}>
                          {selectedAnalysis.ev_call >= 0 ? '+' : ''}${selectedAnalysis.ev_call.toFixed(0)}
                        </span>
                      </div>
                    )}
                    {selectedAnalysis.optimal_action && (
                      <div className="analysis-item">
                        <label>Optimal Action:</label>
                        <span className={`optimal-action ${selectedAnalysis.optimal_action}`}>
                          {selectedAnalysis.optimal_action.toUpperCase()}
                        </span>
                      </div>
                    )}
                    {selectedAnalysis.decision_quality && (
                      <div className="analysis-item quality">
                        <label>Quality:</label>
                        <span className={`quality-badge ${selectedAnalysis.decision_quality}`}>
                          {selectedAnalysis.decision_quality.toUpperCase()}
                        </span>
                      </div>
                    )}
                    {selectedAnalysis.ev_lost != null && selectedAnalysis.ev_lost > 0 && (
                      <div className="analysis-item">
                        <label>EV Lost:</label>
                        <span className="negative">-${selectedAnalysis.ev_lost.toFixed(0)}</span>
                      </div>
                    )}
                  </div>
                </div>
              )}

              {/* Psychology */}
              {selectedAnalysis && renderPsychologySection(selectedAnalysis)}

              {/* Prompt Config */}
              {selectedCapture && renderPromptConfigSection(selectedCapture)}

              <div className="detail-tabs">
                <button
                  className={mode === 'view' ? 'active' : ''}
                  onClick={() => setMode('view')}
                >
                  View
                </button>
                <button
                  className={mode === 'replay' ? 'active' : ''}
                  onClick={() => setMode('replay')}
                >
                  Edit & Replay
                </button>
                <button
                  className={mode === 'interrogate' ? 'active' : ''}
                  onClick={() => {
                    setMode('interrogate');
                    // Initialize interrogation with original response as context
                    if (selectedCapture && interrogationMessages.length === 0) {
                      setInterrogationMessages([{
                        id: 'original-decision',
                        role: 'context',
                        content: selectedCapture.ai_response,
                        timestamp: selectedCapture.created_at,
                      }]);
                    }
                  }}
                >
                  Interrogate
                </button>
              </div>

              {/* Token & Latency Info */}
              {(selectedCapture.input_tokens || selectedCapture.latency_ms) && (
                <div className="token-info">
                  <div className="token-info-row">
                    {(selectedCapture.provider || selectedCapture.model) && (
                      <span>
                        {selectedCapture.provider && <strong>{selectedCapture.provider}</strong>}
                        {selectedCapture.provider && selectedCapture.model && ' / '}
                        {selectedCapture.model}
                        {selectedCapture.reasoning_effort && ` (${selectedCapture.reasoning_effort})`}
                      </span>
                    )}
                    {selectedCapture.latency_ms && <span>Latency: {selectedCapture.latency_ms.toLocaleString()}ms</span>}
                    {selectedCapture.estimated_cost != null && (
                      <span className="cost">Cost: ${selectedCapture.estimated_cost.toFixed(4)}</span>
                    )}
                  </div>
                  <div className="token-info-row">
                    <span className="token-count cached">Cached: {(selectedCapture.cached_tokens ?? 0).toLocaleString()}</span>
                    <span className="token-count input">Input: {((selectedCapture.input_tokens ?? 0) - (selectedCapture.cached_tokens ?? 0)).toLocaleString()}</span>
                    <span className="token-count total-in">Total In: {(selectedCapture.input_tokens ?? 0).toLocaleString()}</span>
                  </div>
                  <div className="token-info-row">
                    <span className="token-count reasoning">Reasoning: {(selectedCapture.reasoning_tokens ?? 0).toLocaleString()}</span>
                    <span className="token-count output">Output: {(selectedCapture.output_tokens ?? 0).toLocaleString()}</span>
                    <span className="token-count total-out">Total Out: {((selectedCapture.reasoning_tokens ?? 0) + (selectedCapture.output_tokens ?? 0)).toLocaleString()}</span>
                  </div>
                </div>
              )}

              {mode === 'view' && (
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

                  {/* Download buttons */}
                  <div className="download-buttons">
                    <button
                      className="download-button"
                      onClick={() => {
                        const request = buildRawRequest(selectedCapture);
                        const filename = `request_${selectedCapture.id}_${selectedCapture.player_name}_h${selectedCapture.hand_number || 0}.json`;
                        downloadJson(request, filename);
                      }}
                    >
                      Download Request
                    </button>
                    {selectedCapture.raw_api_response && (
                      <button
                        className="download-button"
                        onClick={() => {
                          const response = JSON.parse(selectedCapture.raw_api_response!);
                          const filename = `response_${selectedCapture.id}_${selectedCapture.player_name}_h${selectedCapture.hand_number || 0}.json`;
                          downloadJson(response, filename);
                        }}
                      >
                        Download Response
                      </button>
                    )}
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
              )}

              {mode === 'replay' && (
                <div className="replay-editor">
                  <div className="prompt-section">
                    <h4>System Prompt (editable)</h4>
                    <textarea
                      value={modifiedSystemPrompt}
                      onChange={(e) => setModifiedSystemPrompt(e.target.value)}
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
                          onChange={(e) => setUseHistory(e.target.checked)}
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
                              onChange={(e) => updateHistoryMessage(idx, 'role', e.target.value)}
                            >
                              <option value="user">user</option>
                              <option value="assistant">assistant</option>
                              <option value="system">system</option>
                            </select>
                            <textarea
                              value={msg.content}
                              onChange={(e) => updateHistoryMessage(idx, 'content', e.target.value)}
                              rows={3}
                              placeholder="Message content..."
                            />
                            <button
                              className="remove-message"
                              onClick={() => removeHistoryMessage(idx)}
                              title="Remove message"
                            >
                              ×
                            </button>
                          </div>
                        ))}
                        <button className="add-message" onClick={addHistoryMessage}>
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
                      onChange={(e) => setModifiedUserMessage(e.target.value)}
                      rows={15}
                    />
                  </div>

                  {/* Provider, Model, and Reasoning Settings */}
                  <div className="replay-settings">
                    <div className="setting-group">
                      <label>Provider:</label>
                      <select
                        value={replayProvider}
                        onChange={(e) => handleReplayProviderChange(e.target.value)}
                      >
                        {providers.length > 0 ? (
                          providers.map(p => (
                            <option key={p.id} value={p.id}>{p.name}</option>
                          ))
                        ) : (
                          <option value="openai">OpenAI</option>
                        )}
                      </select>
                    </div>
                    <div className="setting-group">
                      <label>Model:</label>
                      <select
                        value={replayModel}
                        onChange={(e) => setReplayModel(e.target.value)}
                      >
                        {getModelsForProviderWithFallback(replayProvider).map(model => (
                          <option key={model} value={model}>{model}</option>
                        ))}
                      </select>
                    </div>
                    <div className="setting-group">
                      <label>Reasoning:</label>
                      <select
                        value={replayReasoningEffort}
                        onChange={(e) => setReplayReasoningEffort(e.target.value)}
                      >
                        {reasoningLevels.map(level => (
                          <option key={level} value={level}>{level}</option>
                        ))}
                      </select>
                    </div>
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
              )}

              {mode === 'interrogate' && (
                <InterrogationChat
                  capture={selectedCapture}
                  messages={interrogationMessages}
                  onMessagesUpdate={setInterrogationMessages}
                  sessionId={interrogationSessionId}
                  onSessionIdUpdate={setInterrogationSessionId}
                  provider={interrogateProvider}
                  onProviderChange={handleInterrogateProviderChange}
                  model={interrogateModel}
                  onModelChange={setInterrogateModel}
                  reasoningEffort={interrogateReasoningEffort}
                  onReasoningEffortChange={setInterrogateReasoningEffort}
                  providers={providers}
                  getModelsForProvider={getModelsForProviderWithFallback}
                  reasoningLevels={reasoningLevels}
                />
              )}
            </>
          ) : (
            <div className="no-selection">
              Select a capture from the list to view details
            </div>
          )}
        </div>
      </div>
      )}

      {/* Mobile Filter Sheet */}
      <MobileFilterSheet
        isOpen={filterSheetOpen}
        onClose={() => setFilterSheetOpen(false)}
        title="Filters"
      >
        <FilterSheetContent
          accentColor="teal"
          onClear={() => {
            setFilters({ limit: 50, offset: 0, labels: undefined, error_type: undefined, has_error: undefined, is_correction: undefined });
            setFilterSheetOpen(false);
          }}
          onApply={() => setFilterSheetOpen(false)}
        >
          <FilterGroup label="Action">
            <select
              className="mobile-filter-sheet__select"
              value={filters.action || ''}
              onChange={(e) => {
                setFilters({ ...filters, action: e.target.value || undefined, offset: 0 });
              }}
            >
              <option value="">All Actions</option>
              <option value="fold">Fold</option>
              <option value="check">Check</option>
              <option value="call">Call</option>
              <option value="raise">Raise</option>
            </select>
          </FilterGroup>

          <FilterGroup label="Phase">
            <select
              className="mobile-filter-sheet__select"
              value={filters.phase || ''}
              onChange={(e) => {
                setFilters({ ...filters, phase: e.target.value || undefined, offset: 0 });
              }}
            >
              <option value="">All Phases</option>
              <option value="PRE_FLOP">Pre-Flop</option>
              <option value="FLOP">Flop</option>
              <option value="TURN">Turn</option>
              <option value="RIVER">River</option>
            </select>
          </FilterGroup>

          <FilterGroup label="Min Pot Odds">
            <input
              type="number"
              className="mobile-filter-sheet__input"
              placeholder="e.g., 3"
              value={filters.min_pot_odds || ''}
              onChange={(e) => {
                setFilters({
                  ...filters,
                  min_pot_odds: e.target.value ? parseFloat(e.target.value) : undefined,
                  offset: 0
                });
              }}
            />
          </FilterGroup>

          <FilterGroup label="Error Type">
            <select
              className="mobile-filter-sheet__select"
              value={filters.error_type || ''}
              onChange={(e) => {
                setFilters({ ...filters, error_type: e.target.value || undefined, offset: 0 });
              }}
            >
              <option value="">All Error Types</option>
              <option value="malformed_json">Malformed JSON</option>
              <option value="missing_field">Missing Field</option>
              <option value="invalid_action">Invalid Action</option>
              <option value="semantic_error">Semantic Error</option>
            </select>
          </FilterGroup>

          <FilterGroup label="Error Status">
            <select
              className="mobile-filter-sheet__select"
              value={filters.has_error === undefined ? '' : filters.has_error.toString()}
              onChange={(e) => {
                setFilters({
                  ...filters,
                  has_error: e.target.value === '' ? undefined : e.target.value === 'true',
                  offset: 0
                });
              }}
            >
              <option value="">All</option>
              <option value="true">Has Error</option>
              <option value="false">No Error</option>
            </select>
          </FilterGroup>

          <FilterGroup label="Correction">
            <select
              className="mobile-filter-sheet__select"
              value={filters.is_correction === undefined ? '' : filters.is_correction.toString()}
              onChange={(e) => {
                setFilters({
                  ...filters,
                  is_correction: e.target.value === '' ? undefined : e.target.value === 'true',
                  offset: 0
                });
              }}
            >
              <option value="">All</option>
              <option value="false">Original Only</option>
              <option value="true">Corrections Only</option>
            </select>
          </FilterGroup>

          <FilterGroup label="Emotion">
            <select
              className="mobile-filter-sheet__select"
              value={filters.display_emotion || ''}
              onChange={(e) => {
                setFilters({
                  ...filters,
                  display_emotion: e.target.value || undefined,
                  offset: 0
                });
              }}
            >
              <option value="">All</option>
              {availableEmotions.map(e => (
                <option key={e} value={e}>{formatEmotionName(e)}</option>
              ))}
            </select>
          </FilterGroup>

          <FilterGroup label="Min Tilt">
            <input
              className="mobile-filter-sheet__input"
              type="number"
              placeholder="Min tilt level"
              value={filters.min_tilt_level ?? ''}
              onChange={(e) => {
                setFilters({
                  ...filters,
                  min_tilt_level: e.target.value ? parseFloat(e.target.value) : undefined,
                  offset: 0
                });
              }}
              min={0}
              max={1}
              step={0.1}
            />
          </FilterGroup>

          <FilterGroup label="Max Tilt">
            <input
              className="mobile-filter-sheet__input"
              type="number"
              placeholder="Max tilt level"
              value={filters.max_tilt_level ?? ''}
              onChange={(e) => {
                setFilters({
                  ...filters,
                  max_tilt_level: e.target.value ? parseFloat(e.target.value) : undefined,
                  offset: 0
                });
              }}
              min={0}
              max={1}
              step={0.1}
            />
          </FilterGroup>

          {/* Label filter chips */}
          {labelStats && Object.keys(labelStats).length > 0 && (
            <FilterGroup label="Labels">
              <div className="debugger-filter-chips">
                {Object.entries(labelStats)
                  .filter(([, count]) => count > 0)
                  .map(([label, count]) => (
                    <button
                      key={label}
                      className={`label-chip label-chip--${getLabelSeverity(label)} ${filters.labels?.includes(label) ? 'label-chip--selected' : ''}`}
                      onClick={() => toggleLabelFilter(label)}
                      type="button"
                    >
                      <span className="label-chip__count">{count}</span>
                      <span className="label-chip__name">{formatLabelName(label)}</span>
                    </button>
                  ))}
              </div>
            </FilterGroup>
          )}
        </FilterSheetContent>
      </MobileFilterSheet>
    </div>
  );

  // If embedded (mobile or desktop), just return the content without PageLayout wrapper
  if (embedded || isMobile) {
    return content;
  }

  // Desktop: wrap in PageLayout
  return (
    <PageLayout variant="top" glowColor="amber" maxWidth="xl">
      <PageHeader
        title="Decision Analyzer"
        subtitle="Analyze and replay AI decision prompts"
        onBack={onBack}
      />
      {content}
    </PageLayout>
  );
}
