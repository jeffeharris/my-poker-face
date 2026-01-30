import { useState, useEffect, useCallback } from 'react';
import { config } from '../config';
import { logger } from '../utils/logger';

export interface UsageStats {
  hands_played: number;
  hands_limit: number;
  hands_limit_reached: boolean;
  max_opponents: number;
  max_active_games: number;
  is_guest: boolean;
}

export function useUsageStats() {
  const [stats, setStats] = useState<UsageStats | null>(null);
  const [loading, setLoading] = useState(true);

  const fetchStats = useCallback(async () => {
    try {
      const response = await fetch(`${config.API_URL}/api/usage-stats`, {
        credentials: 'include',
      });
      if (response.ok) {
        const data = await response.json();
        setStats(data);
      }
    } catch (error) {
      logger.error('Failed to fetch usage stats:', error);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchStats();
  }, [fetchStats]);

  return { stats, loading, refetch: fetchStats };
}
