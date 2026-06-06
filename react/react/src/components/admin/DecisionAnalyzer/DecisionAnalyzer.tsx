import { useState, useEffect, useCallback, useMemo } from 'react';
import { RefreshCw, ChevronLeft, ChevronRight, Globe } from 'lucide-react';
import { PageLayout, PageHeader } from '../../shared';
import { config } from '../../../config';
import { useLLMProviders } from '../../../hooks/useLLMProviders';
import { useViewport } from '../../../hooks/useViewport';
import { useDebouncedValue } from '../../../hooks/useDebouncedValue';
import { logger } from '../../../utils/logger';
import { MobileFilterBar } from '../shared/MobileFilterBar';
import { CollapsibleSection } from '../shared/CollapsibleSection';
import type {
  PromptCapture,
  CaptureStats,
  CaptureFilters,
  ReplayResponse,
  DecisionAnalysisStats,
  ConversationMessage,
  DecisionAnalysis,
  DebugMode,
  InterrogationMessage,
  LabelStats,
} from './types';
import { getActionColor } from './utils';
import { AnalysisStatsBar } from './AnalysisStatsBar';
import { CaptureList } from './CaptureList';
import { CaptureDetailPanel } from './CaptureDetailPanel';
import { DesktopFilterBar } from './DesktopFilterBar';
import { CaptureFilterSheet } from './CaptureFilterSheet';
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

export function DecisionAnalyzer({
  onBack,
  embedded = false,
  onDetailModeChange,
  initialCaptureId,
  onCaptureSelect,
}: DecisionAnalyzerProps) {
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

  // Whether the (game-wide) stats summary is expanded. Collapsible so it can be
  // tucked away to give the list/detail below more room.
  const [statsOpen, setStatsOpen] = useState(true);

  const [captures, setCaptures] = useState<PromptCapture[]>([]);
  const [stats, setStats] = useState<CaptureStats | null>(null);
  const [labelStats, setLabelStats] = useState<LabelStats | null>(null);
  const [analysisStats, setAnalysisStats] = useState<DecisionAnalysisStats | null>(null);
  const [selectedCapture, setSelectedCapture] = useState<PromptCapture | null>(null);
  const [selectedAnalysis, setSelectedAnalysis] = useState<DecisionAnalysis | null>(null);
  const [loading, setLoading] = useState(false);
  const [statsLoading, setStatsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [total, setTotal] = useState(0);
  const [availableEmotions, setAvailableEmotions] = useState<string[]>([]);

  // Filters
  const [filters, setFilters] = useState<CaptureFilters>({
    limit: 50,
    offset: 0,
  });

  // Debounced filters drive the list fetch so typing in the numeric inputs
  // (pot odds, tilt) doesn't fire a request per keystroke.
  const debouncedFilters = useDebouncedValue(filters, 300);

  // Game context falls back capture-first → analysis-second.
  // Commentary captures (both LLM and TieredBot) don't carry pot/stack/hand
  // state on the capture row — that state lives on the linked decision_analysis.
  // For player_decision captures the capture columns are populated, so the
  // capture wins. For commentary captures `phase` literally equals 'commentary',
  // so we explicitly prefer analysis.phase in that case.
  const ctx = useMemo(() => {
    const c = selectedCapture;
    const a = selectedAnalysis;
    const isCommentaryCapture = c?.call_type === 'commentary';

    const parseJsonArray = (s: string | null | undefined): string[] | null => {
      if (!s) return null;
      try {
        const v = JSON.parse(s);
        return Array.isArray(v) ? v : null;
      } catch {
        return null;
      }
    };

    const phase = isCommentaryCapture
      ? (a?.phase ?? c?.phase ?? null)
      : (c?.phase ?? a?.phase ?? null);
    const pot_total = c?.pot_total ?? a?.pot_total ?? null;
    const cost_to_call = c?.cost_to_call ?? a?.cost_to_call ?? null;
    const pot_odds =
      c?.pot_odds ??
      (a?.pot_total != null && a?.cost_to_call && a.cost_to_call > 0
        ? a.pot_total / a.cost_to_call
        : null);
    return {
      phase,
      pot_total,
      cost_to_call,
      pot_odds,
      player_stack: c?.player_stack ?? a?.player_stack ?? null,
      player_hand: c?.player_hand ?? parseJsonArray(a?.player_hand),
      community_cards: c?.community_cards ?? parseJsonArray(a?.community_cards),
      action_taken: c?.action_taken ?? a?.action_taken ?? null,
      raise_amount: c?.raise_amount ?? a?.raise_amount ?? null,
    };
  }, [selectedCapture, selectedAnalysis]);

  // Mode state (view, replay, interrogate)
  const [mode, setMode] = useState<DebugMode>('view');

  // Replay state
  const [modifiedSystemPrompt, setModifiedSystemPrompt] = useState('');
  const [modifiedUserMessage, setModifiedUserMessage] = useState('');
  const [modifiedConversationHistory, setModifiedConversationHistory] = useState<
    ConversationMessage[]
  >([]);
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
  const { providers, getModelsForProvider } = useLLMProviders({ scope: 'system' });
  const reasoningLevels = ['minimal', 'low', 'medium', 'high'];

  const fetchCaptures = useCallback(async () => {
    setLoading(true);
    setError(null);

    try {
      const f = debouncedFilters;
      const params = new URLSearchParams();
      if (f.game_id) params.set('game_id', f.game_id);
      if (f.player_name) params.set('player_name', f.player_name);
      if (f.action) params.set('action', f.action);
      if (f.phase) params.set('phase', f.phase);
      if (f.min_pot_odds !== undefined) params.set('min_pot_odds', f.min_pot_odds.toString());
      if (f.min_pot_size !== undefined) params.set('min_pot_size', f.min_pot_size.toString());
      if (f.max_pot_size !== undefined) params.set('max_pot_size', f.max_pot_size.toString());
      if (f.min_big_blind !== undefined) params.set('min_big_blind', f.min_big_blind.toString());
      if (f.max_big_blind !== undefined) params.set('max_big_blind', f.max_big_blind.toString());
      if (f.labels && f.labels.length > 0) params.set('labels', f.labels.join(','));
      if (f.labelMatchAll) params.set('label_match_all', 'true');
      if (f.error_type) params.set('error_type', f.error_type);
      if (f.has_error !== undefined) params.set('has_error', f.has_error.toString());
      if (f.is_correction !== undefined) params.set('is_correction', f.is_correction.toString());
      if (f.display_emotion) params.set('display_emotion', f.display_emotion);
      if (f.min_tilt_level !== undefined) params.set('min_tilt_level', f.min_tilt_level.toString());
      if (f.max_tilt_level !== undefined) params.set('max_tilt_level', f.max_tilt_level.toString());
      if (f.limit) params.set('limit', f.limit.toString());
      if (f.offset) params.set('offset', f.offset.toString());
      // Skip the expensive bundled stats so the list paints fast (<1s).
      // Stats are loaded separately via fetchCaptureStats().
      params.set('include_stats', 'false');

      const response = await fetch(`${config.API_URL}/api/prompt-debug/captures?${params}`, {
        credentials: 'include',
      });

      if (!response.ok) {
        throw new Error('Failed to fetch captures');
      }

      const data = await response.json();
      setCaptures(data.captures || []);
      setTotal(data.total || 0);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unknown error');
    } finally {
      setLoading(false);
    }
  }, [debouncedFilters]);

  // Lazy-load the capture + label stats separately from the list so the list
  // can paint fast. These run the expensive full-table aggregations.
  const fetchCaptureStats = useCallback(async () => {
    setStatsLoading(true);
    try {
      const params = new URLSearchParams();
      if (filters.game_id) params.set('game_id', filters.game_id);
      // 'all' matches the bundled stats' behavior (no call_type constraint);
      // the standalone endpoints otherwise default to 'player_decision'.
      params.set('call_type', 'all');

      const [statsRes, labelRes] = await Promise.all([
        fetch(`${config.API_URL}/api/prompt-debug/stats?${params}`, {
          credentials: 'include',
        }),
        fetch(`${config.API_URL}/api/prompt-debug/label-stats?${params}`, {
          credentials: 'include',
        }),
      ]);

      if (statsRes.ok) {
        const data = await statsRes.json();
        setStats(data.stats);
      }
      if (labelRes.ok) {
        const data = await labelRes.json();
        setLabelStats(data.label_stats || null);
      }
    } catch (err) {
      // Silently ignore - stats are supplementary to the list
      logger.debug('Failed to fetch capture stats:', err);
    } finally {
      setStatsLoading(false);
    }
  }, [filters.game_id]);

  const fetchAnalysisStats = useCallback(async () => {
    try {
      const params = new URLSearchParams();
      if (filters.game_id) params.set('game_id', filters.game_id);

      const response = await fetch(`${config.API_URL}/api/prompt-debug/analysis-stats?${params}`, {
        credentials: 'include',
      });

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
        const response = await fetch(`${config.API_URL}/api/prompt-debug/emotions`, {
          credentials: 'include',
        });
        if (response.ok) {
          const data = await response.json();
          setAvailableEmotions(data.emotions || []);
        }
      } catch (err) {
        logger.debug('Failed to fetch emotions:', err);
      }
    })();
  }, []);

  // The list refetches on every (debounced) filter change.
  useEffect(() => {
    fetchCaptures();
  }, [fetchCaptures]);

  // Stats are scoped only to game_id, so they're kept in a separate effect —
  // changing action/phase/tilt/etc. no longer re-runs these expensive
  // full-table aggregations (they only re-run when game_id changes).
  useEffect(() => {
    fetchCaptureStats();
    fetchAnalysisStats();
  }, [fetchCaptureStats, fetchAnalysisStats]);

  // Get models for a specific provider (with fallback)
  const getModelsForProviderWithFallback = useCallback(
    (providerId: string): string[] => {
      const models = getModelsForProvider(providerId);
      return models.length > 0 ? models : ['gpt-5-nano', 'gpt-5-mini', 'gpt-5'];
    },
    [getModelsForProvider]
  );

  // Handle provider change for replay
  const handleReplayProviderChange = useCallback(
    (newProvider: string) => {
      setReplayProvider(newProvider);
      const models = getModelsForProviderWithFallback(newProvider);
      if (models.length > 0 && !models.includes(replayModel)) {
        setReplayModel(models[0]);
      }
    },
    [getModelsForProviderWithFallback, replayModel]
  );

  // Handle provider change for interrogate
  const handleInterrogateProviderChange = useCallback(
    (newProvider: string) => {
      setInterrogateProvider(newProvider);
      const models = getModelsForProviderWithFallback(newProvider);
      if (models.length > 0 && !models.includes(interrogateModel)) {
        setInterrogateModel(models[0]);
      }
    },
    [getModelsForProviderWithFallback, interrogateModel]
  );

  const fetchCaptureDetail = async (captureId: number, updateUrl = true) => {
    try {
      const response = await fetch(`${config.API_URL}/api/prompt-debug/captures/${captureId}`, {
        credentials: 'include',
      });

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
    setModifiedConversationHistory((prev) => {
      const updated = [...prev];
      updated[index] = { ...updated[index], [field]: value as ConversationMessage['role'] };
      return updated;
    });
  };

  const removeHistoryMessage = (index: number) => {
    setModifiedConversationHistory((prev) => prev.filter((_, i) => i !== index));
  };

  const addHistoryMessage = () => {
    setModifiedConversationHistory((prev) => [...prev, { role: 'user', content: '' }]);
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
      ? currentLabels.filter((l) => l !== label)
      : [...currentLabels, label];
    setFilters({ ...filters, labels: newLabels.length > 0 ? newLabels : undefined, offset: 0 });
  };

  const activeFilterCount = getActiveFilterCount();

  // Get current capture index in filtered list for prev/next navigation
  const currentCaptureIndex = selectedCapture
    ? captures.findIndex((c) => c.id === selectedCapture.id)
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

  // Shared list panel (left column on desktop, full-width list on mobile)
  const captureList = (
    <CaptureList
      captures={captures}
      total={total}
      loading={loading}
      selectedCapture={selectedCapture}
      filters={filters}
      onFiltersChange={setFilters}
      onSelectCapture={fetchCaptureDetail}
    />
  );

  // Shared detail panel props (desktop side-by-side + mobile full-width view)
  const detailPanelProps = {
    capture: selectedCapture,
    analysis: selectedAnalysis,
    ctx,
    mode,
    onModeChange: setMode,
    onSelectCapture: fetchCaptureDetail,
    modifiedSystemPrompt,
    onSystemPromptChange: setModifiedSystemPrompt,
    modifiedUserMessage,
    onUserMessageChange: setModifiedUserMessage,
    modifiedConversationHistory,
    onUpdateHistoryMessage: updateHistoryMessage,
    onRemoveHistoryMessage: removeHistoryMessage,
    onAddHistoryMessage: addHistoryMessage,
    useHistory,
    onUseHistoryChange: setUseHistory,
    replayProvider,
    onReplayProviderChange: handleReplayProviderChange,
    replayModel,
    onReplayModelChange: setReplayModel,
    replayReasoningEffort,
    onReplayReasoningEffortChange: setReplayReasoningEffort,
    onReplay: handleReplay,
    replaying,
    replayResult,
    providers,
    getModelsForProvider: getModelsForProviderWithFallback,
    reasoningLevels,
    interrogationMessages,
    onInterrogationMessagesUpdate: setInterrogationMessages,
    interrogationSessionId,
    onInterrogationSessionIdUpdate: setInterrogationSessionId,
    interrogateProvider,
    onInterrogateProviderChange: handleInterrogateProviderChange,
    interrogateModel,
    onInterrogateModelChange: setInterrogateModel,
    interrogateReasoningEffort,
    onInterrogateReasoningEffortChange: setInterrogateReasoningEffort,
  };

  const content = (
    <div
      className={`decision-analyzer${embedded ? ' decision-analyzer--embedded' : ''}${isMobile ? ' decision-analyzer--mobile' : ''}${showMobileDetail ? ' decision-analyzer--showing-detail' : ''}`}
    >
      {/* Mobile detail header bar - shows player info and prev/next when viewing detail */}
      {isMobile && showMobileDetail && selectedCapture && (
        <div className="decision-analyzer__detail-bar">
          <div className="decision-analyzer__detail-info">
            <span className="decision-analyzer__detail-player">{selectedCapture.player_name}</span>
            <span
              className={`decision-analyzer__detail-action ${getActionColor(ctx.action_taken)}`}
            >
              {ctx.action_taken?.toUpperCase()}
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
            <span className="decision-analyzer__nav-count">
              {currentCaptureIndex + 1}/{captures.length}
            </span>
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

      {/* Stats still computing - list is already visible above */}
      {statsLoading && !analysisStats && !stats && (!isMobile || !showMobileDetail) && (
        <div className="debugger-stats analysis-stats stats-loading">
          <div className="stat-item">
            <span className="stat-label">Loading stats…</span>
          </div>
        </div>
      )}

      {/* Decision Analysis Stats - hidden on mobile when showing detail.
          Labelled "Global" because the numbers are game-wide, not filtered;
          collapsible to reclaim space for the list/detail below. */}
      {analysisStats && analysisStats.total > 0 && (!isMobile || !showMobileDetail) && (
        <CollapsibleSection
          title="Stats"
          badge="Global"
          icon={<Globe size={16} />}
          isOpen={statsOpen}
          onToggle={() => setStatsOpen((o) => !o)}
        >
          <AnalysisStatsBar
            analysisStats={analysisStats}
            stats={stats}
            labelStats={labelStats}
            filters={filters}
            onToggleLabel={toggleLabelFilter}
          />
        </CollapsibleSection>
      )}

      {/* Filters - desktop only (mobile filters are in header) */}
      {!isMobile && (
        <DesktopFilterBar
          filters={filters}
          onFiltersChange={setFilters}
          availableEmotions={availableEmotions}
          labelStats={labelStats}
          loading={loading}
          onRefresh={fetchCaptures}
          onToggleLabel={toggleLabelFilter}
        />
      )}

      {error && <div className="debugger-error">{error}</div>}

      {/* Mobile: Show list OR detail based on showMobileDetail state */}
      {isMobile ? (
        showMobileDetail ? (
          <CaptureDetailPanel variant="mobile" {...detailPanelProps} />
        ) : (
          captureList
        )
      ) : (
        // Desktop: Side-by-side layout
        <div className="debugger-layout">
          {captureList}
          <CaptureDetailPanel variant="desktop" {...detailPanelProps} />
        </div>
      )}

      {/* Mobile Filter Sheet */}
      <CaptureFilterSheet
        isOpen={filterSheetOpen}
        onClose={() => setFilterSheetOpen(false)}
        filters={filters}
        onFiltersChange={setFilters}
        availableEmotions={availableEmotions}
        labelStats={labelStats}
        onToggleLabel={toggleLabelFilter}
      />
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
