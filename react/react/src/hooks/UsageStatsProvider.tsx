import { useState, useEffect, useCallback } from 'react';
import type { ReactNode } from 'react';
import { config } from '../config';
import { logger } from '../utils/logger';
import { UsageStatsContext } from './useUsageStats';

export function UsageStatsProvider({ children }: { children: ReactNode }) {
  const [stats, setStats] = useState(null);
  const [loading, setLoading] = useState(true);

  const fetchStats = useCallback(async () => {
    try {
      const response = await fetch(`${config.API_URL}/api/usage-stats`, {
        credentials: 'include',
      });
      if (response.ok) {
        const data = await response.json();
        setStats(data);
      } else {
        logger.warn(`Usage stats request failed: ${response.status}`);
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

  return (
    <UsageStatsContext.Provider value={{ stats, loading, refetch: fetchStats }}>
      {children}
    </UsageStatsContext.Provider>
  );
}
