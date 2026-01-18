import { useState, useCallback, useEffect, useRef } from 'react';
import { config } from '../config';

interface UseAdminResourceOptions<T> {
  /** Transform the response data before setting state */
  transform?: (data: unknown) => T;
  /** Auto-fetch on mount (default: true) */
  autoFetch?: boolean;
  /** Dependencies that trigger refetch when changed */
  deps?: unknown[];
  /** Error handler for custom error handling */
  onError?: (error: string) => void;
}

interface UseAdminResourceResult<T> {
  data: T | null;
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
}

/**
 * Hook for fetching admin API resources with consistent loading/error handling.
 *
 * @example
 * // Simple usage
 * const { data: models, loading, error, refresh } = useAdminResource<Model[]>('/admin/api/models');
 *
 * @example
 * // With transform
 * const { data: providers } = useAdminResource<string[]>('/admin/pricing/providers', {
 *   transform: (data) => data.providers.map((p: { provider: string }) => p.provider)
 * });
 *
 * @example
 * // With dependencies (refetch when filter changes)
 * const { data: pricing } = useAdminResource<PricingEntry[]>(
 *   `/admin/pricing?provider=${filterProvider}`,
 *   { deps: [filterProvider] }
 * );
 */
export function useAdminResource<T>(
  endpoint: string,
  options: UseAdminResourceOptions<T> = {}
): UseAdminResourceResult<T> {
  const { transform, autoFetch = true, deps = [], onError } = options;

  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(autoFetch);
  const [error, setError] = useState<string | null>(null);

  // Track if component is mounted to avoid state updates after unmount
  const mountedRef = useRef(true);

  const fetchData = useCallback(async () => {
    setLoading(true);
    setError(null);

    try {
      const response = await fetch(`${config.API_URL}${endpoint}`);
      const result = await response.json();

      if (!mountedRef.current) return;

      if (result.success) {
        // Apply transform if provided, otherwise use the result directly
        // Common patterns: result.models, result.pricing, result.settings, etc.
        const extractedData = transform
          ? transform(result)
          : (result.data ?? result.models ?? result.pricing ?? result.settings ?? result.storage ?? result);
        setData(extractedData as T);
      } else {
        const errorMessage = result.error || 'Failed to load data';
        setError(errorMessage);
        onError?.(errorMessage);
      }
    } catch (err) {
      if (!mountedRef.current) return;
      const errorMessage = 'Failed to connect to server';
      setError(errorMessage);
      onError?.(errorMessage);
    } finally {
      if (mountedRef.current) {
        setLoading(false);
      }
    }
  }, [endpoint, transform, onError]);

  // Auto-fetch on mount and when dependencies change
  useEffect(() => {
    if (autoFetch) {
      fetchData();
    }
  }, [autoFetch, fetchData, ...deps]);

  // Cleanup on unmount
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  return {
    data,
    loading,
    error,
    refresh: fetchData,
  };
}

/**
 * Hook for making admin API mutations (POST, PUT, DELETE) with consistent error handling.
 *
 * @example
 * const { mutate: toggleModel, loading } = useAdminMutation<{ enabled: boolean }>();
 *
 * await toggleModel(`/admin/api/models/${id}/toggle`, { enabled: true });
 */
export function useAdminMutation<TPayload = unknown, TResponse = unknown>() {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const mutate = useCallback(async (
    endpoint: string,
    payload?: TPayload,
    method: 'POST' | 'PUT' | 'DELETE' = 'POST'
  ): Promise<{ success: boolean; data?: TResponse; error?: string }> => {
    setLoading(true);
    setError(null);

    try {
      const response = await fetch(`${config.API_URL}${endpoint}`, {
        method,
        headers: payload ? { 'Content-Type': 'application/json' } : undefined,
        body: payload ? JSON.stringify(payload) : undefined,
      });

      const result = await response.json();

      if (result.success) {
        return { success: true, data: result as TResponse };
      } else {
        const errorMessage = result.error || 'Operation failed';
        setError(errorMessage);
        return { success: false, error: errorMessage };
      }
    } catch (err) {
      const errorMessage = 'Failed to connect to server';
      setError(errorMessage);
      return { success: false, error: errorMessage };
    } finally {
      setLoading(false);
    }
  }, []);

  return { mutate, loading, error };
}
