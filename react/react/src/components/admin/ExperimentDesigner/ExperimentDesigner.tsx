import { useState, useCallback, useEffect, useMemo } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import { History, Sparkles } from 'lucide-react';
import type { InitialMessage } from './ExperimentChat';
import { ConfigPreview } from './ConfigPreview';
import { ReplayConfigPreview } from './ReplayConfigPreview';
import { ExperimentList } from './ExperimentList';
import { MobileExperimentDesign } from './MobileExperimentDesign';
import { useViewport } from '../../../hooks/useViewport';
import type { ExperimentConfig, ExperimentSummary, LabAssistantContext, ConfigVersion, ChatMessage, NextStepSuggestion, ExperimentType, ReplayExperimentConfig } from './types';
import { DEFAULT_EXPERIMENT_CONFIG, DEFAULT_REPLAY_CONFIG } from './types';

/** Extended experiment type with 'undetermined' for initial state */
type ExperimentTypeState = ExperimentType | 'undetermined';
import { adminFetch } from '../../../utils/api';
import { logger } from '../../../utils/logger';
import { generateSeed } from './seedWords';

/** Create a fresh experiment config with a new random seed */
function createFreshConfig(): ExperimentConfig {
  return {
    ...DEFAULT_EXPERIMENT_CONFIG,
    random_seed: generateSeed(),
  };
}

/** Data returned from the /chat/latest endpoint */
interface PendingSession {
  session_id: string;
  messages: ChatMessage[];
  config: ExperimentConfig;
  config_versions: ConfigVersion[] | null;
  updated_at: string;
}

// Interface for experiment detail passed from ExperimentDetail
interface ExperimentDetailForEdit {
  id: number;
  name: string;
  notes: string | null;
  config: ExperimentConfig;
  summary: {
    failed_tournaments?: Array<{
      tournament_id: string;
      tournament_number: number;
      variant: string | null;
      error: string;
      error_type: string;
      duration_seconds: number;
    }>;
  } | null;
}
import './ExperimentDesigner.css';

type ExperimentMode = 'design' | 'list';

/** Props for the assistant panel, passed to parent for page-level rendering */
export interface AssistantPanelProps {
  config: ExperimentConfig;
  sessionId: string | null;
  onSessionIdChange: (sessionId: string) => void;
  onConfigUpdate: (updates: Partial<ExperimentConfig>) => void;
  initialMessage?: InitialMessage | null;
  initialChatHistory?: ChatMessage[];
  configVersions: ConfigVersion[];
  onConfigVersionsChange: (versions: ConfigVersion[]) => void;
  currentVersionIndex: number;
  onCurrentVersionIndexChange: (index: number) => void;
  /** Current experiment type (tournament, replay, or undetermined) */
  experimentType?: ExperimentType | 'undetermined';
  /** Callback when experiment type changes */
  onExperimentTypeChange?: (type: ExperimentType | 'undetermined') => void;
}

interface ExperimentDesignerProps {
  embedded?: boolean;
  /** Callback when assistant panel should be shown/hidden. Pass null to hide. */
  onAssistantPanelChange?: (props: AssistantPanelProps | null) => void;
  /** Callback when design mode changes */
  onDesignModeChange?: (isDesignMode: boolean) => void;
  /** Initial mode - 'list' (default) or 'design' */
  initialMode?: ExperimentMode;
}

// Location state types for navigation from experiment detail
interface LocationState {
  editExperiment?: ExperimentDetailForEdit;
  buildFromSuggestion?: {
    experiment: ExperimentDetailForEdit;
    suggestion: NextStepSuggestion;
  };
}

export function ExperimentDesigner({ embedded = false, onAssistantPanelChange, onDesignModeChange, initialMode = 'list' }: ExperimentDesignerProps) {
  const { isMobile } = useViewport();
  const navigate = useNavigate();
  const location = useLocation();
  const [mode, setMode] = useState<ExperimentMode>(initialMode);
  const [config, setConfig] = useState<ExperimentConfig>(() => createFreshConfig());
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [failureContext, setFailureContext] = useState<LabAssistantContext | null>(null);
  const [configVersions, setConfigVersions] = useState<ConfigVersion[]>([]);
  const [currentVersionIndex, setCurrentVersionIndex] = useState(0);
  const [initialChatHistory, setInitialChatHistory] = useState<ChatMessage[] | undefined>(undefined);

  // Experiment type state - starts as undetermined until user selects via quick action or conversation
  const [experimentType, setExperimentType] = useState<ExperimentTypeState>('undetermined');

  // Replay config state (separate from tournament config)
  const [replayConfig, setReplayConfig] = useState<ReplayExperimentConfig>({ ...DEFAULT_REPLAY_CONFIG });

  // Session resume state
  const [pendingSession, setPendingSession] = useState<PendingSession | null>(null);
  const [showResumePrompt, setShowResumePrompt] = useState(false);

  // Compute initialMessage from failureContext (memoized to prevent unnecessary re-renders)
  const initialMessage = useMemo(() => failureContext ? {
    userMessage: failureContext.type === 'suggestion'
      ? `Build a follow-up experiment to test: "${failureContext.suggestion.hypothesis}"`
      : `Analyze why my experiment "${failureContext.experimentName}" failed and suggest fixes.`,
    context: failureContext,
  } : null, [failureContext]);

  // Notify parent about design mode changes
  useEffect(() => {
    onDesignModeChange?.(mode === 'design' && !isMobile);
  }, [mode, isMobile, onDesignModeChange]);

  // Notify parent about assistant panel state
  useEffect(() => {
    if (!onAssistantPanelChange) return;

    if (mode === 'design' && !isMobile) {
      onAssistantPanelChange({
        config,
        sessionId,
        onSessionIdChange: setSessionId,
        onConfigUpdate: (updates) => setConfig(prev => ({ ...prev, ...updates })),
        initialMessage,
        initialChatHistory,
        configVersions,
        onConfigVersionsChange: setConfigVersions,
        currentVersionIndex,
        onCurrentVersionIndexChange: setCurrentVersionIndex,
        experimentType,
        onExperimentTypeChange: setExperimentType,
      });
    } else {
      onAssistantPanelChange(null);
    }
  }, [mode, isMobile, config, sessionId, initialMessage, initialChatHistory, configVersions, currentVersionIndex, onAssistantPanelChange, experimentType]);

  // Handle navigation state from experiment detail (edit/build actions)
  useEffect(() => {
    const state = location.state as LocationState | null;
    if (!state) return;

    if (state.editExperiment) {
      // Handle "Edit in Lab Assistant" from experiment detail
      const experiment = state.editExperiment;
      const configToEdit: ExperimentConfig = {
        ...experiment.config,
        name: `${experiment.config.name || experiment.name}_v2`,
      };

      setConfig(configToEdit);
      setSessionId(null);
      setConfigVersions([]);
      setCurrentVersionIndex(0);
      setFailureContext({
        type: 'failure',
        experimentId: experiment.id,
        experimentName: experiment.name,
        errorMessage: experiment.notes || 'Unknown error',
        failedTournaments: experiment.summary?.failed_tournaments || [],
      });
      setMode('design');

      // Clear the state to prevent re-triggering
      navigate(location.pathname, { replace: true, state: null });
    } else if (state.buildFromSuggestion) {
      // Handle "Build from Suggestion" from experiment detail
      const { experiment, suggestion } = state.buildFromSuggestion;
      const configToEdit: ExperimentConfig = {
        ...experiment.config,
        name: `${experiment.config.name || experiment.name}_followup`,
        parent_experiment_id: experiment.id,
      };

      setConfig(configToEdit);
      setSessionId(null);
      setConfigVersions([]);
      setCurrentVersionIndex(0);
      setFailureContext({
        type: 'suggestion',
        experimentId: experiment.id,
        experimentName: experiment.name,
        suggestion,
        parentConfig: experiment.config,
      });
      setMode('design');

      // Clear the state to prevent re-triggering
      navigate(location.pathname, { replace: true, state: null });
    }
  }, [location.state, location.pathname, navigate]);

  // Fetch latest chat session on mount
  useEffect(() => {
    const fetchLatestSession = async () => {
      try {
        const response = await adminFetch('/api/experiments/chat/latest');
        if (!response.ok) {
          return;
        }
        const data = await response.json();
        if (data.success && data.session) {
          setPendingSession(data.session);
        }
      } catch (error) {
        logger.error('Error fetching latest chat session:', error);
      }
    };
    fetchLatestSession();
  }, []);

  // Show resume prompt when navigating directly to design mode with a pending session
  useEffect(() => {
    if (initialMode === 'design' && pendingSession && !showResumePrompt) {
      setShowResumePrompt(true);
    }
  }, [initialMode, pendingSession, showResumePrompt]);

  const handleConfigUpdate = useCallback((updates: Partial<ExperimentConfig>) => {
    setConfig(prev => ({ ...prev, ...updates }));
  }, []);

  const handleReplayConfigUpdate = useCallback((updates: Partial<ReplayExperimentConfig>) => {
    setReplayConfig(prev => ({ ...prev, ...updates }));
  }, []);

  const handleResumeSession = useCallback(() => {
    if (!pendingSession) return;

    // Load the pending session data
    setConfig(pendingSession.config);
    setSessionId(pendingSession.session_id);
    setInitialChatHistory(pendingSession.messages);
    setConfigVersions(pendingSession.config_versions || []);
    setCurrentVersionIndex(pendingSession.config_versions ? pendingSession.config_versions.length - 1 : 0);
    setFailureContext(null);
    setShowResumePrompt(false);
    setPendingSession(null);
    setMode('design');
  }, [pendingSession]);

  const handleStartFresh = useCallback(async () => {
    // Archive the pending session
    if (pendingSession) {
      try {
        await adminFetch('/api/experiments/chat/archive', {
          method: 'POST',
          body: JSON.stringify({ session_id: pendingSession.session_id }),
        });
      } catch (error) {
        logger.error('Error archiving session:', error);
      }
    }

    // Start fresh with new seed
    setConfig(createFreshConfig());
    setReplayConfig({ ...DEFAULT_REPLAY_CONFIG });  // Reset replay config
    setSessionId(null);
    setFailureContext(null);
    setConfigVersions([]);
    setCurrentVersionIndex(0);
    setInitialChatHistory(undefined);
    setShowResumePrompt(false);
    setPendingSession(null);
    setExperimentType('undetermined');  // Reset experiment type
    setMode('design');
  }, [pendingSession]);

  const handleViewExperiment = useCallback((experiment: ExperimentSummary) => {
    // Route to the correct detail page based on experiment type
    if (experiment.experiment_type === 'replay') {
      navigate(`/admin/replays/${experiment.id}`);
    } else {
      navigate(`/admin/experiments/${experiment.id}`);
    }
  }, [navigate]);

  const handleBackToList = useCallback(() => {
    setMode('list');
  }, []);

  const handleExperimentLaunched = useCallback(() => {
    setMode('list');
    setConfig(createFreshConfig());
    setReplayConfig({ ...DEFAULT_REPLAY_CONFIG });  // Reset replay config
    setSessionId(null);
    setFailureContext(null);
    setConfigVersions([]);
    setCurrentVersionIndex(0);
    setExperimentType('undetermined');  // Reset experiment type
  }, []);


  const handleVersionChange = useCallback((index: number) => {
    if (index >= 0 && index < configVersions.length) {
      // Simply navigate to the selected version
      // Auto-saving was too fragile due to form state drift (e.g., model select
      // showing empty before providers load). Users can preserve edits by
      // sending a chat message, which creates a new version.
      setCurrentVersionIndex(index);
      setConfig(configVersions[index].config);
    }
  }, [configVersions]);

  // Mobile design mode - full screen with tabs
  if (isMobile && mode === 'design') {
    return (
      <div className={`experiment-designer experiment-designer--mobile ${embedded ? 'experiment-designer--embedded' : ''}`}>
        <MobileExperimentDesign
          config={config}
          sessionId={sessionId}
          onSessionIdChange={setSessionId}
          onConfigUpdate={handleConfigUpdate}
          onLaunch={handleExperimentLaunched}
          onBack={handleBackToList}
          experimentType={experimentType}
          onExperimentTypeChange={setExperimentType}
          initialMessage={initialMessage}
          initialChatHistory={initialChatHistory}
          configVersions={configVersions}
          onConfigVersionsChange={setConfigVersions}
          currentVersionIndex={currentVersionIndex}
          onCurrentVersionIndexChange={setCurrentVersionIndex}
          onVersionChange={handleVersionChange}
        />
      </div>
    );
  }

  return (
    <div className={`experiment-designer ${embedded ? 'experiment-designer--embedded' : ''}`}>
      {/* Mode Content */}
      <div className="experiment-designer__content">
        {mode === 'design' && experimentType === 'replay' && (
          <ReplayConfigPreview
            config={replayConfig}
            onConfigUpdate={handleReplayConfigUpdate}
            onLaunch={handleExperimentLaunched}
            sessionId={sessionId}
          />
        )}
        {mode === 'design' && experimentType !== 'replay' && (
          <ConfigPreview
            config={config}
            onConfigUpdate={handleConfigUpdate}
            onLaunch={handleExperimentLaunched}
            sessionId={sessionId}
            configVersions={configVersions}
            currentVersionIndex={currentVersionIndex}
            onVersionChange={handleVersionChange}
          />
        )}

        {mode === 'list' && (
          <ExperimentList
            onViewExperiment={handleViewExperiment}
          />
        )}
      </div>

      {/* Resume Session Prompt Modal */}
      {showResumePrompt && pendingSession && (
        <div className="experiment-designer__resume-overlay">
          <div className="experiment-designer__resume-modal">
            <div className="experiment-designer__resume-icon">
              <History size={32} />
            </div>
            <h3 className="experiment-designer__resume-title">Resume Previous Session?</h3>
            <p className="experiment-designer__resume-text">
              You have an unfinished experiment design session from{' '}
              <strong>{new Date(pendingSession.updated_at).toLocaleString()}</strong>.
            </p>
            {pendingSession.config.name && (
              <p className="experiment-designer__resume-config">
                Experiment: <strong>{pendingSession.config.name}</strong>
              </p>
            )}
            <p className="experiment-designer__resume-messages">
              {pendingSession.messages.length} messages in conversation
            </p>
            <div className="experiment-designer__resume-actions">
              <button
                className="experiment-designer__resume-btn experiment-designer__resume-btn--continue"
                onClick={handleResumeSession}
                type="button"
              >
                <History size={18} />
                Continue Session
              </button>
              <button
                className="experiment-designer__resume-btn experiment-designer__resume-btn--fresh"
                onClick={handleStartFresh}
                type="button"
              >
                <Sparkles size={18} />
                Start Fresh
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default ExperimentDesigner;
