import { useState, useCallback, useEffect, useRef } from 'react';
import type { CoachStats, CoachMessage, CoachMode } from '../types/coach';
import { config } from '../config';

const MAX_MESSAGES = 50;
const PROACTIVE_TIP_MARKER = '__proactive_tip__';

interface UseCoachOptions {
  gameId: string | null;
  playerName: string;
  isPlayerTurn: boolean;
  enabled: boolean;
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
}

function loadMode(): CoachMode {
  try {
    const stored = localStorage.getItem('coach_mode');
    if (stored === 'proactive' || stored === 'reactive' || stored === 'off') {
      return stored;
    }
  } catch { /* ignore */ }
  return 'reactive';
}

export function useCoach({
  gameId,
  playerName,
  isPlayerTurn,
  enabled,
}: UseCoachOptions): UseCoachResult {
  const [mode, setModeState] = useState<CoachMode>(loadMode);
  const [stats, setStats] = useState<CoachStats | null>(null);
  const [messages, setMessages] = useState<CoachMessage[]>([]);
  const [isThinking, setIsThinking] = useState(false);
  const [proactiveTip, setProactiveTip] = useState<string | null>(null);
  const [handReviewPending, setHandReviewPending] = useState(false);
  const [hasUnreadReview, setHasUnreadReview] = useState(false);

  // Track whether we've already fetched for this turn
  const fetchedForTurn = useRef(false);
  const prevIsPlayerTurn = useRef(false);

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
    if (!gameId || !enabled) return;
    try {
      const res = await fetch(`${config.API_URL}/api/coach/${gameId}/stats`, {
        credentials: 'include',
      });
      if (res.ok) {
        const data = await res.json();
        setStats(data);
      }
    } catch {
      /* non-critical */
    }
  }, [gameId, enabled]);

  const fetchProactiveTip = useCallback(async () => {
    if (!gameId) return;
    try {
      const res = await fetch(`${config.API_URL}/api/coach/${gameId}/ask`, {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question: PROACTIVE_TIP_MARKER }),
      });
      if (res.ok) {
        const data = await res.json();
        setProactiveTip(data.answer);
        if (data.stats) setStats(data.stats);
      }
    } catch {
      /* non-critical */
    }
  }, [gameId]);

  const sendQuestion = useCallback(async (question: string) => {
    if (!gameId || !enabled) return;

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
        body: JSON.stringify({ question }),
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
  }, [gameId, enabled]);

  const clearProactiveTip = useCallback(() => {
    setProactiveTip(null);
  }, []);

  const fetchHandReview = useCallback(async () => {
    if (!gameId || handReviewPending) return;
    setHandReviewPending(true);
    try {
      const res = await fetch(`${config.API_URL}/api/coach/${gameId}/hand-review`, {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
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
      }
    } catch {
      /* non-critical */
    } finally {
      setHandReviewPending(false);
    }
  }, [gameId, handReviewPending]);

  const clearUnreadReview = useCallback(() => {
    setHasUnreadReview(false);
  }, []);

  // When player's turn starts, auto-fetch stats (and proactive tip if enabled)
  useEffect(() => {
    if (isPlayerTurn && !prevIsPlayerTurn.current) {
      // Turn just started
      fetchedForTurn.current = false;
    }
    prevIsPlayerTurn.current = isPlayerTurn;

    if (!isPlayerTurn || !enabled || fetchedForTurn.current) return;
    fetchedForTurn.current = true;

    refreshStats();

    if (mode === 'proactive') {
      fetchProactiveTip();
    }
  }, [isPlayerTurn, enabled, mode, refreshStats, fetchProactiveTip]);

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
  };
}
