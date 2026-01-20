import { useState, useCallback, useEffect } from 'react';
import { Plus, ArrowLeft, History, Sparkles } from 'lucide-react';
import { ExperimentChat } from './ExperimentChat';
import { ConfigPreview } from './ConfigPreview';
import { ExperimentList } from './ExperimentList';
import { ExperimentDetail } from './ExperimentDetail';
import { MobileExperimentDesign } from './MobileExperimentDesign';
import { useViewport } from '../../../hooks/useViewport';
import type { ExperimentConfig, ExperimentSummary, LabAssistantContext, ConfigVersion, ChatMessage, NextStepSuggestion } from './types';
import { DEFAULT_EXPERIMENT_CONFIG } from './types';
import { config as appConfig } from '../../../config';

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

type ExperimentMode = 'design' | 'list' | 'detail';

interface ExperimentDesignerProps {
  embedded?: boolean;
}

export function ExperimentDesigner({ embedded = false }: ExperimentDesignerProps) {
  const { isMobile } = useViewport();
  const [mode, setMode] = useState<ExperimentMode>('list');
  const [config, setConfig] = useState<ExperimentConfig>(DEFAULT_EXPERIMENT_CONFIG);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [selectedExperimentId, setSelectedExperimentId] = useState<number | null>(null);
  const [failureContext, setFailureContext] = useState<LabAssistantContext | null>(null);
  const [configVersions, setConfigVersions] = useState<ConfigVersion[]>([]);
  const [currentVersionIndex, setCurrentVersionIndex] = useState(0);
  const [initialChatHistory, setInitialChatHistory] = useState<ChatMessage[] | undefined>(undefined);

  // Session resume state
  const [pendingSession, setPendingSession] = useState<PendingSession | null>(null);
  const [showResumePrompt, setShowResumePrompt] = useState(false);

  // Fetch latest chat session on mount
  useEffect(() => {
    const fetchLatestSession = async () => {
      try {
        const response = await fetch(`${appConfig.apiUrl}/api/experiments/chat/latest`);
        if (!response.ok) return;
        const data = await response.json();
        if (data.success && data.session) {
          setPendingSession(data.session);
        }
      } catch (error) {
        console.error('Error fetching latest chat session:', error);
      }
    };
    fetchLatestSession();
  }, []);

  const handleConfigUpdate = useCallback((updates: Partial<ExperimentConfig>) => {
    setConfig(prev => ({ ...prev, ...updates }));
  }, []);

  const handleNewExperiment = useCallback(() => {
    // Check if there's a pending session to resume
    if (pendingSession) {
      setShowResumePrompt(true);
      return;
    }
    // No pending session, start fresh
    setConfig(DEFAULT_EXPERIMENT_CONFIG);
    setSessionId(null);
    setFailureContext(null);
    setConfigVersions([]);
    setCurrentVersionIndex(0);
    setInitialChatHistory(undefined);
    setMode('design');
  }, [pendingSession]);

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
        await fetch(`${appConfig.apiUrl}/api/experiments/chat/archive`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ session_id: pendingSession.session_id }),
        });
      } catch (error) {
        console.error('Error archiving session:', error);
      }
    }

    // Start fresh
    setConfig(DEFAULT_EXPERIMENT_CONFIG);
    setSessionId(null);
    setFailureContext(null);
    setConfigVersions([]);
    setCurrentVersionIndex(0);
    setInitialChatHistory(undefined);
    setShowResumePrompt(false);
    setPendingSession(null);
    setMode('design');
  }, [pendingSession]);

  const handleViewExperiment = useCallback((experiment: ExperimentSummary) => {
    setSelectedExperimentId(experiment.id);
    setMode('detail');
  }, []);

  const handleBackToList = useCallback(() => {
    setSelectedExperimentId(null);
    setMode('list');
  }, []);

  const handleExperimentLaunched = useCallback(() => {
    setMode('list');
    setConfig(DEFAULT_EXPERIMENT_CONFIG);
    setSessionId(null);
    setFailureContext(null);
    setConfigVersions([]);
    setCurrentVersionIndex(0);
  }, []);

  const handleEditInLabAssistant = useCallback((experiment: ExperimentDetailForEdit) => {
    // Create a new config based on the failed experiment's config
    const configToEdit: ExperimentConfig = {
      ...experiment.config,
      name: `${experiment.config.name || experiment.name}_v2`,  // Suggest new name
    };

    setConfig(configToEdit);
    setSessionId(null);  // Fresh chat session
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
  }, []);

  const handleBuildFromSuggestion = useCallback((experiment: ExperimentDetailForEdit, suggestion: NextStepSuggestion) => {
    // Create a new config based on the parent experiment's config
    const configToEdit: ExperimentConfig = {
      ...experiment.config,
      name: `${experiment.config.name || experiment.name}_followup`,  // Suggest follow-up name
      parent_experiment_id: experiment.id,  // Track lineage
    };

    setConfig(configToEdit);
    setSessionId(null);  // Fresh chat session
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
  }, []);

  const handleVersionChange = useCallback((index: number) => {
    if (index >= 0 && index < configVersions.length) {
      setCurrentVersionIndex(index);
      setConfig(configVersions[index].config);
    }
  }, [configVersions]);

  // Compute initialMessage from failureContext (cleared after first render via state)
  const initialMessage = failureContext ? {
    userMessage: failureContext.type === 'suggestion'
      ? `Build a follow-up experiment to test: "${failureContext.suggestion.hypothesis}"`
      : `Analyze why my experiment "${failureContext.experimentName}" failed and suggest fixes.`,
    context: failureContext,
  } : null;

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
      {/* Mode Header - hidden on mobile for list/detail (handled by parent) */}
      {(!isMobile || mode === 'design') && (
        <div className="experiment-designer__header">
          {mode === 'design' && (
            <>
              <button
                className="experiment-designer__back-btn"
                onClick={handleBackToList}
                type="button"
              >
                <ArrowLeft size={16} />
                Back to List
              </button>
              <h3 className="experiment-designer__title">Design New Experiment</h3>
            </>
          )}
          {mode === 'list' && (
            <>
              <h3 className="experiment-designer__title">Experiments</h3>
              <button
                className="experiment-designer__new-btn"
                onClick={handleNewExperiment}
                type="button"
              >
                <Plus size={16} />
                New Experiment
              </button>
            </>
          )}
          {mode === 'detail' && (
            <>
              <button
                className="experiment-designer__back-btn"
                onClick={handleBackToList}
                type="button"
              >
                <ArrowLeft size={16} />
                Back to List
              </button>
              <h3 className="experiment-designer__title">Experiment Details</h3>
            </>
          )}
        </div>
      )}

      {/* Mode Content */}
      <div className="experiment-designer__content">
        {mode === 'design' && (
          <div className="experiment-designer__design-layout">
            <div className="experiment-designer__chat-panel">
              <ExperimentChat
                config={config}
                sessionId={sessionId}
                onSessionIdChange={setSessionId}
                onConfigUpdate={handleConfigUpdate}
                initialMessage={initialMessage}
                initialChatHistory={initialChatHistory}
                configVersions={configVersions}
                onConfigVersionsChange={setConfigVersions}
                currentVersionIndex={currentVersionIndex}
                onCurrentVersionIndexChange={setCurrentVersionIndex}
              />
            </div>
            <div className="experiment-designer__preview-panel">
              <ConfigPreview
                config={config}
                onConfigUpdate={handleConfigUpdate}
                onLaunch={handleExperimentLaunched}
                sessionId={sessionId}
                configVersions={configVersions}
                currentVersionIndex={currentVersionIndex}
                onVersionChange={handleVersionChange}
              />
            </div>
          </div>
        )}

        {mode === 'list' && (
          <ExperimentList
            onViewExperiment={handleViewExperiment}
            onNewExperiment={handleNewExperiment}
          />
        )}

        {mode === 'detail' && selectedExperimentId && (
          <ExperimentDetail
            experimentId={selectedExperimentId}
            onBack={handleBackToList}
            onEditInLabAssistant={handleEditInLabAssistant}
            onBuildFromSuggestion={handleBuildFromSuggestion}
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
