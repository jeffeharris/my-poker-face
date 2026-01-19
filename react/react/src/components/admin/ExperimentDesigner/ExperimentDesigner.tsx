import { useState, useCallback } from 'react';
import { Plus, ArrowLeft } from 'lucide-react';
import { ExperimentChat } from './ExperimentChat';
import { ConfigPreview } from './ConfigPreview';
import { ExperimentList } from './ExperimentList';
import { ExperimentDetail } from './ExperimentDetail';
import { MobileExperimentDesign } from './MobileExperimentDesign';
import { useViewport } from '../../../hooks/useViewport';
import type { ExperimentConfig, ExperimentSummary, FailureContext, ConfigVersion } from './types';
import { DEFAULT_EXPERIMENT_CONFIG } from './types';

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
  const [failureContext, setFailureContext] = useState<FailureContext | null>(null);
  const [configVersions, setConfigVersions] = useState<ConfigVersion[]>([]);
  const [currentVersionIndex, setCurrentVersionIndex] = useState(0);

  const handleConfigUpdate = useCallback((updates: Partial<ExperimentConfig>) => {
    setConfig(prev => ({ ...prev, ...updates }));
  }, []);

  const handleNewExperiment = useCallback(() => {
    setConfig(DEFAULT_EXPERIMENT_CONFIG);
    setSessionId(null);
    setFailureContext(null);
    setConfigVersions([]);
    setCurrentVersionIndex(0);
    setMode('design');
  }, []);

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
      experimentId: experiment.id,
      experimentName: experiment.name,
      errorMessage: experiment.notes || 'Unknown error',
      failedTournaments: experiment.summary?.failed_tournaments || [],
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
    userMessage: `Analyze why my experiment "${failureContext.experimentName}" failed and suggest fixes.`,
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
          />
        )}
      </div>
    </div>
  );
}

export default ExperimentDesigner;
