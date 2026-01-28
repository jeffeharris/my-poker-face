/**
 * Hook for fetching LLM providers and their models.
 *
 * Supports two scopes:
 * - 'user': Models available to end users (for game setup)
 * - 'system': Models available to admins (for experiments, debugging)
 */
import { useState, useEffect, useCallback } from 'react';
import { config } from '../config';
import { logger } from '../utils/logger';
import type { ProviderInfo, ModelScope, LLMProvidersResponse } from '../types/llm';

interface UseLLMProvidersOptions {
  /**
   * The scope determines which models are fetched:
   * - 'user': Fetches from /api/user-models (enabled=1 AND user_enabled=1)
   * - 'system': Fetches from /api/system-models (enabled=1, includes system-only models)
   */
  scope: ModelScope;
}

interface UseLLMProvidersResult {
  /** List of available providers with their models */
  providers: ProviderInfo[];
  /** Whether the data is currently loading */
  loading: boolean;
  /** Error message if fetch failed */
  error: string | null;
  /** The default provider ID */
  defaultProvider: string;
  /** Manually refresh the providers list */
  refresh: () => Promise<void>;
  /** Get models for a specific provider */
  getModelsForProvider: (providerId: string) => string[];
  /** Get the default model for a specific provider */
  getDefaultModel: (providerId: string) => string;
  /** Check if a provider supports reasoning */
  providerSupportsReasoning: (providerId: string) => boolean;
  /** Get the cost tier for a model (e.g., '$', '$$', '$$$') */
  getModelTier: (providerId: string, model: string) => string;
  /** Format model name with its cost tier */
  formatModelLabel: (providerId: string, model: string) => string;
}

// Default fallback providers if fetch fails
const FALLBACK_PROVIDERS: ProviderInfo[] = [
  {
    id: 'openai',
    name: 'OpenAI',
    models: ['gpt-5-nano', 'gpt-5-mini', 'gpt-5'],
    default_model: 'gpt-5-nano',
    capabilities: { supports_reasoning: true, supports_json_mode: true, supports_image_generation: true },
  },
];

export function useLLMProviders({ scope }: UseLLMProvidersOptions): UseLLMProvidersResult {
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [defaultProvider, setDefaultProvider] = useState('openai');

  const fetchProviders = useCallback(async () => {
    setLoading(true);
    setError(null);

    // Choose endpoint based on scope
    const endpoint = scope === 'system' ? '/api/system-models' : '/api/user-models';

    try {
      const response = await fetch(`${config.API_URL}${endpoint}`, {
        credentials: 'include',
      });

      if (!response.ok) {
        throw new Error(`Failed to fetch providers: ${response.status}`);
      }

      const data: LLMProvidersResponse = await response.json();

      if (data.providers?.length > 0) {
        setProviders(data.providers);
        setDefaultProvider(data.default_provider || 'openai');
      } else {
        // Use fallback if no providers returned
        setProviders(FALLBACK_PROVIDERS);
      }
    } catch (err) {
      logger.warn(`Failed to fetch providers from ${endpoint}, using fallback:`, err);
      setError(err instanceof Error ? err.message : 'Unknown error');
      setProviders(FALLBACK_PROVIDERS);
    } finally {
      setLoading(false);
    }
  }, [scope]);

  useEffect(() => {
    fetchProviders();
  }, [fetchProviders]);

  const getModelsForProvider = useCallback(
    (providerId: string): string[] => {
      const provider = providers.find((p) => p.id === providerId);
      return provider?.models || [];
    },
    [providers]
  );

  const getDefaultModel = useCallback(
    (providerId: string): string => {
      const provider = providers.find((p) => p.id === providerId);
      return provider?.default_model || '';
    },
    [providers]
  );

  const providerSupportsReasoning = useCallback(
    (providerId: string): boolean => {
      const provider = providers.find((p) => p.id === providerId);
      return provider?.capabilities?.supports_reasoning ?? false;
    },
    [providers]
  );

  const getModelTier = useCallback(
    (providerId: string, model: string): string => {
      const provider = providers.find((p) => p.id === providerId);
      return provider?.model_tiers?.[model] || '';
    },
    [providers]
  );

  const formatModelLabel = useCallback(
    (providerId: string, model: string): string => {
      const tier = getModelTier(providerId, model);
      return tier ? `${model} (${tier})` : model;
    },
    [getModelTier]
  );

  return {
    providers,
    loading,
    error,
    defaultProvider,
    refresh: fetchProviders,
    getModelsForProvider,
    getDefaultModel,
    providerSupportsReasoning,
    getModelTier,
    formatModelLabel,
  };
}

export default useLLMProviders;
