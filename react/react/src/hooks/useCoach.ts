import { useState, useCallback, useEffect, useRef } from 'react';
import type { CoachStats, CoachMessage, CoachMode, CoachProgression, ProgressionState, SkillProgress } from '../types/coach';
import { config } from '../config';

const MAX_MESSAGES = 50;

interface UseCoachOptions {
  gameId: string | null;
  playerName: string;
  isPlayerTurn: boolean;
}

interface UseCoachResult {
  mode: CoachMode;
  setMode: (mode: CoachMode) => void;
  stats: CoachStats | null;
  messages: CoachMessage[];
  isThinking: boolean;
  sendQuestion: (question: string) => Promise<void>;
  refreshStats: () => Promise<void>;
  proactiveTip: string | null;
  clearProactiveTip: () => void;
  handReviewPending: boolean;
  hasUnreadReview: boolean;
  fetchHandReview: () => Promise<void>;
  clearUnreadReview: () => void;
  progression: CoachProgression | null;
  progressionFull: ProgressionState | null;
  skillUnlockQueue: string[];
  fetchProgression: () => Promise<void>;
  skipAhead: (level: string) => Promise<void>;
  dismissSkillUnlock: (skillId: string) => void;
}

function loadLocalMode(): CoachMode {
  try {
    const stored = localStorage.getItem('coach_mode');
    if (stored === 'proactive' || stored === 'reactive' || stored === 'off') {
      return stored;
    }
  } catch { /* ignore */ }
  return 'off';
}

export function useCoach({
  gameId,
  playerName,
  isPlayerTurn,
}: UseCoachOptions): UseCoachResult {
  const [mode, setModeState] = useState<CoachMode>(loadLocalMode);
  const [stats, setStats] = useState<CoachStats | null>(null);
  const [messages, setMessages] = useState<CoachMessage[]>([]);
  const [isThinking, setIsThinking] = useState(false);
  const [proactiveTip, setProactiveTip] = useState<string | null>(null);
  const [handReviewPending, setHandReviewPending] = useState(false);
  const [hasUnreadReview, setHasUnreadReview] = useState(false);
  const [progression, setProgression] = useState<CoachProgression | null>(null);
  const [progressionFull, setProgressionFull] = useState<ProgressionState | null>(null);
  const [skillUnlockQueue, setSkillUnlockQueue] = useState<string[]>([]);

  // Track whether we've already fetched for this turn
  const fetchedForTurn = useRef(false);
  const prevIsPlayerTurn = useRef(false);
  const handReviewInFlightRef = useRef(false);
  const prevSkillStatesRef = useRef<Record<string, SkillProgress>>({});

  // Load coach mode from server when gameId is set
  useEffect(() => {
    if (!gameId) return;
    fetch(`${config.API_URL}/api/coach/${gameId}/config`, {
      credentials: 'include',
    })
      .then(res => res.ok ? res.json() : null)
      .then(data => {
        if (data?.mode && (data.mode === 'proactive' || data.mode === 'reactive' || data.mode === 'off')) {
          setModeState(data.mode);
          try { localStorage.setItem('coach_mode', data.mode); } catch { /* ignore */ }
        }
      })
      .catch(() => { /* non-critical — localStorage fallback already applied */ });
  }, [gameId]);

  const setMode = useCallback((newMode: CoachMode) => {
    setModeState(newMode);
    try {
      localStorage.setItem('coach_mode', newMode);
    } catch { /* ignore */ }

    // Persist to backend
    if (gameId) {
      fetch(`${config.API_URL}/api/coach/${gameId}/config`, {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode: newMode }),
      }).catch(() => { /* non-critical */ });
    }
  }, [gameId]);

  const refreshStats = useCallback(async () => {
    if (!gameId || mode === 'off') return;
    try {
      const res = await fetch(`${config.API_URL}/api/coach/${gameId}/stats`, {
        credentials: 'include',
      });
      if (res.ok) {
        const data = await res.json();
        setStats(data);

        // Extract progression from stats response
        if (data.progression) {
          const prog = data.progression as CoachProgression;
          setProgression(prog);

          // Detect newly appearing skill IDs for unlock toast
          const prevIds = Object.keys(prevSkillStatesRef.current);
          if (prevIds.length > 0) {
            const newIds = Object.keys(prog.skill_states).filter(
              sid => !prevSkillStatesRef.current[sid]
            );
            if (newIds.length > 0) {
              setSkillUnlockQueue(prev => [...prev, ...newIds]);
            }
          }
          prevSkillStatesRef.current = prog.skill_states;
        }
      }
    } catch {
      /* non-critical */
    }
  }, [gameId, mode]);

  const fetchProactiveTip = useCallback(async () => {
    if (!gameId) return;
    try {
      const res = await fetch(`${config.API_URL}/api/coach/${gameId}/ask`, {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ type: 'proactive_tip', playerName }),
      });
      if (res.ok) {
        const data = await res.json();
        setProactiveTip(data.answer);
        const tipMsg: CoachMessage = {
          id: `tip-${Date.now()}`,
          role: 'coach',
          content: data.answer,
          timestamp: Date.now(),
          type: 'tip',
        };
        setMessages(prev => [...prev, tipMsg].slice(-MAX_MESSAGES));
        if (data.stats) setStats(data.stats);
      }
    } catch {
      /* non-critical */
    }
  }, [gameId, playerName]);

  const sendQuestion = useCallback(async (question: string) => {
    if (!gameId || mode === 'off') return;

    const userMsg: CoachMessage = {
      id: `user-${Date.now()}`,
      role: 'user',
      content: question,
      timestamp: Date.now(),
    };

    setMessages(prev => [...prev, userMsg].slice(-MAX_MESSAGES));
    setIsThinking(true);

    try {
      const res = await fetch(`${config.API_URL}/api/coach/${gameId}/ask`, {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question, playerName }),
      });

      if (res.ok) {
        const data = await res.json();
        const coachMsg: CoachMessage = {
          id: `coach-${Date.now()}`,
          role: 'coach',
          content: data.answer,
          timestamp: Date.now(),
        };
        setMessages(prev => [...prev, coachMsg].slice(-MAX_MESSAGES));
        if (data.stats) setStats(data.stats);
      }
    } catch {
      const errorMsg: CoachMessage = {
        id: `coach-err-${Date.now()}`,
        role: 'coach',
        content: 'Sorry, I couldn\'t process that. Try again in a moment.',
        timestamp: Date.now(),
      };
      setMessages(prev => [...prev, errorMsg].slice(-MAX_MESSAGES));
    } finally {
      setIsThinking(false);
    }
  }, [gameId, mode, playerName]);

  const clearProactiveTip = useCallback(() => {
    setProactiveTip(null);
  }, []);

  const fetchHandReview = useCallback(async () => {
    if (!gameId || handReviewInFlightRef.current) return;
    handReviewInFlightRef.current = true;
    setHandReviewPending(true);
    try {
      const res = await fetch(`${config.API_URL}/api/coach/${gameId}/hand-review`, {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ playerName }),
      });
      if (res.ok) {
        const data = await res.json();
        const reviewMsg: CoachMessage = {
          id: `review-${Date.now()}`,
          role: 'coach',
          content: data.review,
          timestamp: Date.now(),
          type: 'review',
        };
        setMessages(prev => [...prev, reviewMsg].slice(-MAX_MESSAGES));
        setHasUnreadReview(true);
        // Refresh stats after hand review so progression bar reflects post-action evaluation
        refreshStats();
      }
    } catch {
      /* non-critical */
    } finally {
      handReviewInFlightRef.current = false;
      setHandReviewPending(false);
    }
  }, [gameId, playerName, refreshStats]);

  const clearUnreadReview = useCallback(() => {
    setHasUnreadReview(false);
  }, []);

  const fetchProgression = useCallback(async () => {
    if (!gameId) return;
    try {
      const res = await fetch(`${config.API_URL}/api/coach/${gameId}/progression`, {
        credentials: 'include',
      });
      if (res.ok) {
        const data = await res.json();
        setProgressionFull(data as ProgressionState);
      }
    } catch {
      /* non-critical */
    }
  }, [gameId]);

  const skipAhead = useCallback(async (level: string) => {
    if (!gameId) return;
    try {
      const res = await fetch(`${config.API_URL}/api/coach/${gameId}/onboarding`, {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ level }),
      });
      if (res.ok) {
        // Re-fetch both stats and full progression after onboarding
        await Promise.all([refreshStats(), fetchProgression()]);
      }
    } catch (err) {
      console.error('skipAhead failed:', err);
    }
  }, [gameId, refreshStats, fetchProgression]);

  const dismissSkillUnlock = useCallback((skillId: string) => {
    setSkillUnlockQueue(prev => prev.filter(id => id !== skillId));
  }, []);

  // When player's turn starts, auto-fetch stats (and proactive tip if enabled)
  useEffect(() => {
    if (isPlayerTurn && !prevIsPlayerTurn.current) {
      // Turn just started
      fetchedForTurn.current = false;
    }
    prevIsPlayerTurn.current = isPlayerTurn;

    if (!isPlayerTurn || mode === 'off' || fetchedForTurn.current) return;
    fetchedForTurn.current = true;

    refreshStats();

    if (mode === 'proactive') {
      fetchProactiveTip();
    }
  }, [isPlayerTurn, mode, refreshStats, fetchProactiveTip]);

  // Clear proactive tip when turn ends
  useEffect(() => {
    if (!isPlayerTurn) {
      setProactiveTip(null);
    }
  }, [isPlayerTurn]);

  return {
    mode,
    setMode,
    stats,
    messages,
    isThinking,
    sendQuestion,
    refreshStats,
    proactiveTip,
    clearProactiveTip,
    handReviewPending,
    hasUnreadReview,
    fetchHandReview,
    clearUnreadReview,
    progression,
    progressionFull,
    skillUnlockQueue,
    fetchProgression,
    skipAhead,
    dismissSkillUnlock,
  };
}
