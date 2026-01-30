import { useContext, createContext } from 'react';

export interface UsageStats {
  hands_played: number;
  hands_limit: number;
  hands_limit_reached: boolean;
  max_opponents: number;
  max_active_games: number;
  is_guest: boolean;
}

export interface UsageStatsContextValue {
  stats: UsageStats | null;
  loading: boolean;
  refetch: () => Promise<void>;
}

export const UsageStatsContext = createContext<UsageStatsContextValue | null>(null);

export function useUsageStats(): UsageStatsContextValue {
  const context = useContext(UsageStatsContext);
  if (!context) {
    throw new Error('useUsageStats must be used within a UsageStatsProvider');
  }
  return context;
}
