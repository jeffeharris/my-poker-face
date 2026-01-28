import { useState, useEffect, useCallback } from 'react';
import { config } from '../config';
import { logger } from '../utils/logger';
import type { CareerStats, TournamentHistoryEntry, EliminatedPersonality } from '../types/tournament';

interface UseCareerStatsResult {
  stats: CareerStats | null;
  tournaments: TournamentHistoryEntry[];
  eliminatedPersonalities: EliminatedPersonality[];
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
}

export function useCareerStats(): UseCareerStatsResult {
  const [stats, setStats] = useState<CareerStats | null>(null);
  const [tournaments, setTournaments] = useState<TournamentHistoryEntry[]>([]);
  const [eliminatedPersonalities, setEliminatedPersonalities] = useState<EliminatedPersonality[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchStats = useCallback(async () => {
    setLoading(true);
    setError(null);

    try {
      const response = await fetch(`${config.API_URL}/api/career-stats`, {
        credentials: 'include',
      });

      if (!response.ok) {
        if (response.status === 401) {
          setError('Please log in to view your stats');
        } else {
          setError('Failed to load stats');
        }
        setStats(null);
        setTournaments([]);
        setEliminatedPersonalities([]);
        return;
      }

      const data = await response.json();
      setStats(data.stats || null);
      setTournaments(data.recent_tournaments || []);
      setEliminatedPersonalities(data.eliminated_personalities || []);
    } catch (err) {
      logger.error('Failed to fetch career stats:', err);
      setError('Failed to connect to server');
      setStats(null);
      setTournaments([]);
      setEliminatedPersonalities([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchStats();
  }, [fetchStats]);

  return {
    stats,
    tournaments,
    eliminatedPersonalities,
    loading,
    error,
    refresh: fetchStats,
  };
}
