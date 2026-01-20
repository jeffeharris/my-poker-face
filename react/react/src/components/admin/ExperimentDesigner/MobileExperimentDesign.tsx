import { useState, useEffect, useRef } from 'react';
import { MessageSquare, Settings, ArrowLeft, CheckCircle } from 'lucide-react';
import { ExperimentChat, type InitialMessage } from './ExperimentChat';
import { ConfigPreview } from './ConfigPreview';
import type { ExperimentConfig, ConfigVersion, ChatMessage } from './types';
import './MobileExperimentDesign.css';

type DesignTab = 'chat' | 'configure';

interface MobileExperimentDesignProps {
  config: ExperimentConfig;
  sessionId: string | null;
  onSessionIdChange: (sessionId: string) => void;
  onConfigUpdate: (updates: Partial<ExperimentConfig>) => void;
  onLaunch: () => void;
  onBack: () => void;
  initialMessage?: InitialMessage | null;
  initialChatHistory?: ChatMessage[];
  configVersions?: ConfigVersion[];
  onConfigVersionsChange?: (versions: ConfigVersion[]) => void;
  currentVersionIndex?: number;
  onCurrentVersionIndexChange?: (index: number) => void;
  onVersionChange?: (index: number) => void;
}

/**
 * MobileExperimentDesign - Mobile-optimized experiment design mode
 *
 * Features:
 * - Segmented control to switch between Chat and Configure
 * - Full-screen chat experience
 * - Full-screen config editor with form/JSON toggle
 * - Config status indicator showing experiment name
 */
export function MobileExperimentDesign({
  config,
  sessionId,
  onSessionIdChange,
  onConfigUpdate,
  onLaunch,
  onBack,
  initialMessage,
  initialChatHistory,
  configVersions,
  onConfigVersionsChange,
  currentVersionIndex,
  onCurrentVersionIndexChange,
  onVersionChange,
}: MobileExperimentDesignProps) {
  const [activeTab, setActiveTab] = useState<DesignTab>('chat');
  const [showConfigUpdated, setShowConfigUpdated] = useState(false);
  const prevConfigRef = useRef(config);

  // Show a brief indicator when config changes (from chat)
  useEffect(() => {
    if (prevConfigRef.current !== config && activeTab === 'chat') {
      // Check if something meaningful changed
      const prevName = prevConfigRef.current.name;
      const newName = config.name;
      if (newName && newName !== prevName) {
        setShowConfigUpdated(true);
        const timer = setTimeout(() => setShowConfigUpdated(false), 2000);
        return () => clearTimeout(timer);
      }
    }
    prevConfigRef.current = config;
  }, [config, activeTab]);

  const hasConfig = Boolean(config.name);

  return (
    <div className="mobile-experiment-design">
      {/* Header with back button and segmented control */}
      <header className="mobile-experiment-design__header">
        <button
          className="mobile-experiment-design__back-btn"
          onClick={onBack}
          type="button"
          aria-label="Back to list"
        >
          <ArrowLeft size={20} />
        </button>

        <div className="mobile-experiment-design__tabs">
          <button
            className={`mobile-experiment-design__tab ${activeTab === 'chat' ? 'mobile-experiment-design__tab--active' : ''}`}
            onClick={() => setActiveTab('chat')}
            type="button"
          >
            <MessageSquare size={16} />
            <span>Chat</span>
          </button>
          <button
            className={`mobile-experiment-design__tab ${activeTab === 'configure' ? 'mobile-experiment-design__tab--active' : ''}`}
            onClick={() => setActiveTab('configure')}
            type="button"
          >
            <Settings size={16} />
            <span>Configure</span>
            {hasConfig && <span className="mobile-experiment-design__tab-dot" />}
          </button>
        </div>

        {/* Spacer to balance the back button */}
        <div className="mobile-experiment-design__header-spacer" />
      </header>

      {/* Config status bar - shows when experiment has a name, tappable */}
      {hasConfig && activeTab === 'chat' && (
        <button
          className={`mobile-experiment-design__status ${showConfigUpdated ? 'mobile-experiment-design__status--updated' : ''}`}
          onClick={() => setActiveTab('configure')}
          type="button"
        >
          <CheckCircle size={14} />
          <span className="mobile-experiment-design__status-name">{config.name}</span>
          <span className="mobile-experiment-design__status-hint">
            {showConfigUpdated ? 'Config updated!' : 'Tap to review →'}
          </span>
        </button>
      )}

      {/* Content area - full height for active tab */}
      <div className="mobile-experiment-design__content">
        {activeTab === 'chat' && (
          <div className="mobile-experiment-design__chat-container">
            <ExperimentChat
              config={config}
              sessionId={sessionId}
              onSessionIdChange={onSessionIdChange}
              onConfigUpdate={onConfigUpdate}
              initialMessage={initialMessage}
              initialChatHistory={initialChatHistory}
              configVersions={configVersions}
              onConfigVersionsChange={onConfigVersionsChange}
              currentVersionIndex={currentVersionIndex}
              onCurrentVersionIndexChange={onCurrentVersionIndexChange}
            />
          </div>
        )}

        {activeTab === 'configure' && (
          <div className="mobile-experiment-design__config-container">
            <ConfigPreview
              config={config}
              onConfigUpdate={onConfigUpdate}
              onLaunch={onLaunch}
              configVersions={configVersions}
              currentVersionIndex={currentVersionIndex}
              onVersionChange={onVersionChange}
            />
          </div>
        )}
      </div>
    </div>
  );
}

export default MobileExperimentDesign;
