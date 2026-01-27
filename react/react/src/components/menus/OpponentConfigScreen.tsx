import { Settings } from 'lucide-react';
import { PageLayout, PageHeader } from '../shared';
import type { ProviderInfo, OpponentLLMConfig, OpponentConfig } from '../../types/llm';
import { GAME_MODES } from '../../constants/gameModes';
import './OpponentConfigScreen.css';

interface OpponentConfigScreenProps {
  selectedOpponents: string[];
  providers: ProviderInfo[];
  providersLoading: boolean;
  defaultConfig: OpponentLLMConfig;
  defaultGameMode: string;
  opponentConfigs: Record<string, OpponentConfig>;
  onConfigChange: (name: string, config: OpponentConfig | null) => void;
  onBack: () => void;
}

export function OpponentConfigScreen({
  selectedOpponents,
  providers,
  providersLoading,
  defaultConfig,
  defaultGameMode,
  opponentConfigs,
  onConfigChange,
  onBack,
}: OpponentConfigScreenProps) {
  const getModelsForProvider = (providerId: string): string[] => {
    const provider = providers.find(p => p.id === providerId);
    return provider?.models || [];
  };

  const providerSupportsReasoning = (providerId: string): boolean => {
    const provider = providers.find(p => p.id === providerId);
    return provider?.capabilities?.supports_reasoning ?? false;
  };

  const formatModelLabel = (providerId: string, model: string): string => {
    const provider = providers.find(p => p.id === providerId);
    const tier = provider?.model_tiers?.[model] || '';
    return tier ? `${model} (${tier})` : model;
  };

  const getEffectiveConfig = (opponentName: string): OpponentConfig => {
    return opponentConfigs[opponentName] || defaultConfig;
  };

  const hasCustomConfig = (opponentName: string): boolean => {
    return opponentName in opponentConfigs;
  };

  const handleProviderChange = (opponentName: string, newProvider: string) => {
    const currentConfig = getEffectiveConfig(opponentName);
    const provider = providers.find(p => p.id === newProvider);

    if (provider) {
      const newModel = provider.models.includes(currentConfig.model)
        ? currentConfig.model
        : provider.default_model;

      const newConfig: OpponentConfig = {
        provider: newProvider,
        model: newModel,
      };

      if (provider.capabilities?.supports_reasoning) {
        newConfig.reasoning_effort = currentConfig.reasoning_effort || 'minimal';
      }

      onConfigChange(opponentName, newConfig);
    }
  };

  const handleModelChange = (opponentName: string, newModel: string) => {
    const currentConfig = getEffectiveConfig(opponentName);
    onConfigChange(opponentName, {
      ...currentConfig,
      model: newModel,
    });
  };

  const handleReasoningChange = (opponentName: string, newReasoning: string) => {
    const currentConfig = getEffectiveConfig(opponentName);
    onConfigChange(opponentName, {
      ...currentConfig,
      reasoning_effort: newReasoning,
    });
  };

  const handleGameModeChange = (opponentName: string, newMode: string) => {
    const currentConfig = getEffectiveConfig(opponentName);
    if (newMode === '') {
      // "Default" selected — remove game_mode override
      const { game_mode: _, ...rest } = currentConfig;
      // If nothing else is customized, reset entirely
      const isDefault = rest.provider === defaultConfig.provider
        && rest.model === defaultConfig.model
        && (rest.reasoning_effort || 'minimal') === (defaultConfig.reasoning_effort || 'minimal');
      onConfigChange(opponentName, isDefault ? null : rest);
    } else {
      onConfigChange(opponentName, {
        ...currentConfig,
        game_mode: newMode,
      });
    }
  };

  const handleResetToDefault = (opponentName: string) => {
    onConfigChange(opponentName, null);
  };

  if (providersLoading) {
    return (
      <PageLayout variant="top" glowColor="sapphire" maxWidth="lg">
        <PageHeader
          title="Opponent AI"
          subtitle="Set provider and model per opponent"
          onBack={onBack}
          titleVariant="primary"
        />
        <div className="opponent-config-loading">Loading providers...</div>
      </PageLayout>
    );
  }

  return (
    <PageLayout variant="top" glowColor="sapphire" maxWidth="lg">
      <PageHeader
        title="Opponent AI"
        subtitle="Set provider and model per opponent"
        onBack={onBack}
        titleVariant="primary"
      />

      <div className="opponent-config-content">
        <div className="opponent-config-table">
          {selectedOpponents.map(opponentName => {
            const config = getEffectiveConfig(opponentName);
            const isCustom = hasCustomConfig(opponentName);
            const supportsReasoning = providerSupportsReasoning(config.provider);

            return (
              <div key={opponentName} className={`opponent-row ${isCustom ? 'custom' : ''}`}>
                <div className="config-opponent-name">
                  <span>{opponentName}</span>
                  {isCustom && <Settings size={14} className="custom-indicator" />}
                </div>

                <div className="opponent-settings">
                  <span className="setting-label">Game Mode</span>
                  <select
                    className="setting-select"
                    value={config.game_mode || ''}
                    onChange={(e) => handleGameModeChange(opponentName, e.target.value)}
                  >
                    <option value="">Default ({defaultGameMode.charAt(0).toUpperCase() + defaultGameMode.slice(1)})</option>
                    {GAME_MODES.map(gm => (
                      <option key={gm.value} value={gm.value}>{gm.label}</option>
                    ))}
                  </select>

                  <span className="setting-label">Provider</span>
                  <select
                    className="setting-select"
                    value={config.provider}
                    onChange={(e) => handleProviderChange(opponentName, e.target.value)}
                  >
                    {providers.map(p => (
                      <option key={p.id} value={p.id}>{p.name}</option>
                    ))}
                  </select>

                  <span className="setting-label">Model</span>
                  <select
                    className="setting-select"
                    value={config.model}
                    onChange={(e) => handleModelChange(opponentName, e.target.value)}
                  >
                    {getModelsForProvider(config.provider).map(model => (
                      <option key={model} value={model}>{formatModelLabel(config.provider, model)}</option>
                    ))}
                  </select>

                  {supportsReasoning && (
                    <>
                      <span className="setting-label">Reasoning</span>
                      <select
                        className="setting-select"
                        value={config.reasoning_effort || 'minimal'}
                        onChange={(e) => handleReasoningChange(opponentName, e.target.value)}
                      >
                        <option value="minimal">Minimal</option>
                        <option value="low">Low</option>
                      </select>
                    </>
                  )}

                  <button
                    className="reset-btn"
                    onClick={() => handleResetToDefault(opponentName)}
                    disabled={!isCustom}
                  >
                    Reset to Default
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      </div>

      <div className="opponent-config-footer">
        <button className="done-button" onClick={onBack}>
          Done
        </button>
      </div>
    </PageLayout>
  );
}
