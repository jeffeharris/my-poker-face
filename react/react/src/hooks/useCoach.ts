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
  coachAction: string | null;  // Coach's explicit recommendation (only from /ask endpoint)
  coachRaiseTo: number | null;  // Coach's suggested raise amount
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
  // Track coach's explicit recommendation (only set when coach provides advice via /ask)
  const [coachAction, setCoachAction] = useState<string | null>(null);
  const [coachRaiseTo, setCoachRaiseTo] = useState<number | null>(null);
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
  // Track if coach endpoint is unavailable (404) to avoid repeated failed requests
  const coachUnavailableRef = useRef(false);

  // Reset coach unavailable flag when gameId changes
  useEffect(() => {
    coachUnavailableRef.current = false;
  }, [gameId]);

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

  const refreshStats = useCallback(async () => {
    if (!gameId || mode === 'off' || coachUnavailableRef.current) return;
    try {
      const res = await fetch(`${config.API_URL}/api/coach/${gameId}/stats`, {
        credentials: 'include',
      });
      if (res.status === 404) {
        // Coach endpoint not available for this game - stop polling
        coachUnavailableRef.current = true;
        return;
      }
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

          // Keep full progression in sync so detail view matches strip
          fetchProgression();
        }
      }
    } catch {
      /* non-critical */
    }
  }, [gameId, mode, fetchProgression]);

  const fetchProactiveTip = useCallback(async () => {
    if (!gameId) return;
    setIsThinking(true);
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
        // Store coach's explicit recommendation (separate from GTO stats)
        setCoachAction(data.coach_action ?? null);
        setCoachRaiseTo(data.coach_raise_to ?? null);
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
    } finally {
      setIsThinking(false);
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
        // Store coach's explicit recommendation for reactive mode highlighting
        setCoachAction(data.coach_action ?? null);
        setCoachRaiseTo(data.coach_raise_to ?? null);
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

  // When player's turn starts, auto-fetch stats (and proactive tip if enabled).
  // Debounce to avoid duplicate fetches from rapid game-state socket updates
  // that can briefly toggle isPlayerTurn multiple times.
  useEffect(() => {
    if (isPlayerTurn && !prevIsPlayerTurn.current) {
      // Turn just started
      fetchedForTurn.current = false;
    }
    prevIsPlayerTurn.current = isPlayerTurn;

    if (!isPlayerTurn || mode === 'off' || fetchedForTurn.current) return;

    const timer = setTimeout(() => {
      if (fetchedForTurn.current) return; // already fetched during debounce window
      fetchedForTurn.current = true;

      refreshStats();

      if (mode === 'proactive') {
        fetchProactiveTip();
      }
    }, 300);

    return () => clearTimeout(timer);
  }, [isPlayerTurn, mode, refreshStats, fetchProactiveTip]);

  // Clear proactive tip and coach recommendation when turn ends
  useEffect(() => {
    if (!isPlayerTurn) {
      setProactiveTip(null);
      setCoachAction(null);
      setCoachRaiseTo(null);
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
    coachAction,
    coachRaiseTo,
  };
}
