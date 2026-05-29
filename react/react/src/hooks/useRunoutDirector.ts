import { useEffect, useMemo, useRef } from 'react';
import { RUNOUT_TIMING } from '../constants/runoutTiming';
import type { RunoutSchedule, RunoutStep } from '../types/runout';

interface UseRunoutDirectorParams {
  /** Per-card reaction schedule from the backend (`runout_schedule`), or null. */
  schedule: RunoutSchedule | null;
  /** True during an all-in run-out (auto-dealt remaining streets). */
  runItOut: boolean | undefined;
  /** True once opponent hole cards are revealed (the matchup is on the table). */
  revealed: boolean;
  /** How many community cards are currently shown. */
  communityCardCount: number;
  /** Store hand number — a change means a new hand; reset direction. */
  handNumber: number;
  /** Fast-forward toggle; read at fire time so a mid-run-out toggle isn't stale. */
  fastForward: boolean;
  /** Apply one AI face (sets avatar_emotion + rewrites the avatar URL). Stable. */
  applyReaction: (playerName: string, emotion: string) => void;
  /** Mark/clear the director as the authoritative reaction source (store flag). Stable. */
  setActive: (active: boolean) => void;
}

/**
 * useRunoutDirector — owns the *per-card* avatar-reaction beat of a mobile all-in
 * run-out, the Phase 2 payoff over Phase 1's street granularity. Sibling to
 * `useInterhandDirector`.
 *
 * Design (see docs/plans/RUNOUT_REVEAL_DIRECTOR.md, option B): the backend keeps
 * pacing the *board* street-by-street (one client-agnostic path; desktop is
 * unchanged). This director adds only the finer reaction timing on top — it
 * walks the schedule and, as each street's cards land, fires that card's
 * reaction on a beat aligned to the community-card slide cascade. So a player can
 * light up on the flop card that hit them and stay flat on the others, instead
 * of one lumped street reaction.
 *
 * While directing it sets a store flag so the socket layer drops the backend's
 * street-level `is_reaction` avatar updates (which would otherwise clobber these
 * finer faces). A safety cap releases that ownership even if the board stalls.
 *
 * It does NOT touch the board, the GATED card buffer, or the result beat — those
 * stay backend-paced under option B, so there's nothing here to flash or hang.
 */
export function useRunoutDirector({
  schedule,
  runItOut,
  revealed,
  communityCardCount,
  handNumber,
  fastForward,
  applyReaction,
  setActive,
}: UseRunoutDirectorParams): void {
  // Index steps by phase so a run-out that starts post-flop (no FLOP steps) just
  // finds empty buckets for the streets it skipped.
  const stepsByPhase = useMemo(() => {
    const map: Record<string, RunoutStep[]> = {};
    for (const step of schedule?.steps ?? []) {
      (map[step.phase] ??= []).push(step);
    }
    return map;
  }, [schedule]);

  const playedRef = useRef<Set<string>>(new Set());
  const timersRef = useRef<number[]>([]);
  const directingScheduleRef = useRef<RunoutSchedule | null>(null);
  const prevCountRef = useRef(communityCardCount);
  // FF read at fire time, not closure-captured (avoids a stale value if toggled
  // mid-run-out — the board emits no per-street state to refresh the closure).
  const ffRef = useRef(fastForward);
  ffRef.current = fastForward;

  const clearTimers = () => {
    timersRef.current.forEach((id) => clearTimeout(id));
    timersRef.current = [];
  };
  const scale = (ms: number) => Math.round(ms * (ffRef.current ? RUNOUT_TIMING.ffMultiplier : 1));
  const at = (ms: number, fn: () => void) => {
    timersRef.current.push(window.setTimeout(fn, scale(ms)));
  };
  const applyStep = (step: RunoutStep | undefined) => {
    step?.reactions.forEach((r) => applyReaction(r.player_name, r.emotion));
  };

  // Activate on a fresh run-out (new schedule object), tear down when it ends.
  // A stale schedule from the previous hand keeps the same object identity, so
  // it never re-activates; a new hand's schedule is a new object and does.
  useEffect(() => {
    const directing = directingScheduleRef.current;
    if (schedule && runItOut && schedule !== directing) {
      clearTimers();
      playedRef.current = new Set();
      // Cards already on the board (dealt during betting, before the all-in
      // locked) are the baseline — don't re-react to them.
      prevCountRef.current = communityCardCount;
      directingScheduleRef.current = schedule;
      setActive(true);
      // Safety net: release reaction ownership even if the board never finishes
      // (deleted/abandoned game), so backend reactions aren't muted forever. Not
      // FF-shrunk — it's a backstop, not a beat.
      timersRef.current.push(window.setTimeout(() => setActive(false), RUNOUT_TIMING.safetyCapMs));
    } else if ((!runItOut || !schedule) && directing) {
      clearTimers();
      playedRef.current = new Set();
      directingScheduleRef.current = null;
      prevCountRef.current = communityCardCount;
      setActive(false);
    }
    // communityCardCount intentionally omitted — only (re)activation inputs drive this.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [schedule, runItOut, handNumber, setActive]);

  // INITIAL (matchup read) fires once the hole cards are revealed, timed to land
  // as the reveal cascade settles.
  useEffect(() => {
    if (!revealed || !schedule || directingScheduleRef.current !== schedule) return;
    if (playedRef.current.has('INITIAL:0')) return;
    playedRef.current.add('INITIAL:0');
    const step = stepsByPhase.INITIAL?.[0];
    at(RUNOUT_TIMING.initialReactionDelayMs, () => applyStep(step));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [revealed, schedule, stepsByPhase]);

  // Per-street batch: when a street's cards land, schedule each card's reaction
  // at its cascade offset (flop card i at i × stagger). After the river, queue
  // the showdown lock-up and release reaction ownership.
  useEffect(() => {
    if (!schedule || directingScheduleRef.current !== schedule) return;
    const prev = prevCountRef.current;
    if (communityCardCount <= prev) {
      prevCountRef.current = communityCardCount;
      return;
    }
    prevCountRef.current = communityCardCount;

    const phase =
      communityCardCount === 3
        ? 'FLOP'
        : communityCardCount === 4
          ? 'TURN'
          : communityCardCount === 5
            ? 'RIVER'
            : null;
    if (!phase) return;

    const steps = stepsByPhase[phase] ?? [];
    let lastCardIndex = 0;
    for (const step of steps) {
      const key = `${phase}:${step.card_index}`;
      if (playedRef.current.has(key)) continue;
      playedRef.current.add(key);
      lastCardIndex = Math.max(lastCardIndex, step.card_index);
      at(step.card_index * RUNOUT_TIMING.perCardStaggerMs + RUNOUT_TIMING.reactionAfterCardMs, () =>
        applyStep(step)
      );
    }

    if (phase === 'RIVER' && !playedRef.current.has('SHOWDOWN:0')) {
      playedRef.current.add('SHOWDOWN:0');
      const showdown = stepsByPhase.SHOWDOWN?.[0];
      const showdownAt =
        lastCardIndex * RUNOUT_TIMING.perCardStaggerMs + RUNOUT_TIMING.showdownReactionDelayMs;
      at(showdownAt, () => applyStep(showdown));
      // Stay authoritative through the lock-up beat, THEN hand off — releasing
      // in the same tick as the showdown face let the next state push revert it
      // instantly ("changed then switched back").
      at(showdownAt + RUNOUT_TIMING.showdownHoldMs, () => setActive(false));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [schedule, communityCardCount, stepsByPhase]);

  // Clear any pending timers + reaction ownership on unmount.
  useEffect(() => {
    return () => {
      clearTimers();
      setActive(false);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
}
