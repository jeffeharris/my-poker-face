import { useEffect, useRef, useState } from 'react';
import type { Socket } from 'socket.io-client';
import toast from 'react-hot-toast';

import { getInvite } from '../components/cash/tournamentApi';

/**
 * Main Event registration-window countdown toasts.
 *
 * The circuit Main Event invite opens a ~10-minute registration window
 * (`expires_at`); when it lapses the event starts without the player. This
 * surfaces two "starts in N min" toasts (at 5 min and 1 min remaining) so the
 * deadline reaches the player wherever they are — the lobby card AND the felt
 * while they're seated at a cash game.
 *
 * Split into a pure firing engine (`useCountdownToasts`) and a game-page data
 * source (`useMainEventInviteSignal`):
 *   - The Lobby already holds the open invite in state, so it feeds the engine
 *     directly — no extra network.
 *   - The game page has no invite source, so it learns the deadline from a
 *     one-shot fetch on socket-connect plus the realtime `main_event_invite`
 *     push (emitted by the world ticker when it offers one). This avoids
 *     polling `GET /api/tournament/invite`, which is expensive (sandbox lock +
 *     chairman sweep) and rate-limited at tick frequency.
 *
 * Dedup is module-level (keyed by invite + threshold) so navigating
 * lobby↔game — which swaps one page socket for another — never re-fires a
 * threshold already announced on the other surface.
 */

interface Threshold {
  /** Seconds remaining at/under which this toast fires. */
  secs: number;
  message: string;
}

/** Descending — the engine relies on this order to derive each threshold's
 *  lower bound (so a backgrounded tab that jumps past 5:00 straight into the
 *  1-min zone fires only the accurate "1 minute" toast, not a stale "5 minutes"). */
const THRESHOLDS: Threshold[] = [
  { secs: 300, message: 'Main Event starts in 5 minutes' },
  { secs: 60, message: 'Main Event starts in 1 minute — register now' },
];

/** Thresholds already announced, keyed `${inviteId}:${secs}`. Module-level so it
 *  outlives any single hook instance (route changes remount the consumers).
 *  Grows by at most one key per threshold per invite — negligible over a session
 *  (invites are minutes apart). */
const announced = new Set<string>();

/** Test-only: clear the dedup set so suites don't leak fired thresholds across
 *  cases. Not used in app code (the set is intentionally process-lived there). */
export function _resetAnnouncedForTest(): void {
  announced.clear();
}

/**
 * Fire the countdown toasts as `expiresAt` is crossed. Pure: no fetching, no
 * socket — give it the current open invite's expiry + id and it does the rest.
 * Pass nulls when there's no open invite (the timer simply idles).
 */
export function useCountdownToasts(expiresAt: string | null, inviteId: string | null): void {
  // Last observed remaining-seconds, so we fire on the downward *crossing* of a
  // threshold rather than every tick below it (and never fire late on first
  // sight of an invite already past a threshold).
  const prevRemainingRef = useRef<number | null>(null);

  useEffect(() => {
    prevRemainingRef.current = null;
    if (!expiresAt || !inviteId) return;
    const expiryMs = new Date(expiresAt).getTime();
    if (Number.isNaN(expiryMs)) return;

    const check = () => {
      const remaining = Math.round((expiryMs - Date.now()) / 1000);
      const prev = prevRemainingRef.current;
      THRESHOLDS.forEach((t, i) => {
        // Lower bound = the next (smaller) threshold, or 0 — fire `t` only while
        // remaining is still inside its band, so a big tick gap doesn't trigger a
        // higher threshold's now-stale message.
        const floor = THRESHOLDS[i + 1]?.secs ?? 0;
        const key = `${inviteId}:${t.secs}`;
        if (
          prev != null &&
          prev > t.secs &&
          remaining <= t.secs &&
          remaining > floor &&
          !announced.has(key)
        ) {
          announced.add(key);
          toast(t.message, { icon: '🏆', duration: 6000 });
        }
      });
      prevRemainingRef.current = remaining;
    };

    check(); // seed prevRemaining (prev is null → never fires on this first call)
    const id = setInterval(check, 1000);
    return () => clearInterval(id);
  }, [expiresAt, inviteId]);
}

interface InviteSignal {
  inviteId: string | null;
  expiresAt: string | null;
}

interface UseMainEventInviteSignalParams {
  /** The game-page socket (owned by usePokerGame). May be null until connected. */
  socketRef: React.MutableRefObject<Socket | null>;
  /** usePokerGame's connection flag — re-attach + re-seed when this flips true. */
  connected: boolean;
}

/**
 * Track the open Main Event invite's deadline from the game page: a one-shot
 * fetch on connect (catches an invite already open before the player sat down)
 * plus the realtime `main_event_invite` push (catches one offered mid-session).
 */
export function useMainEventInviteSignal({
  socketRef,
  connected,
}: UseMainEventInviteSignalParams): InviteSignal {
  const [signal, setSignal] = useState<InviteSignal>({ inviteId: null, expiresAt: null });

  // One-shot seed on connect. Best-effort: the push covers anything offered after
  // we joined, so a failed fetch just means we rely on that.
  useEffect(() => {
    if (!connected) return;
    let cancelled = false;
    void getInvite()
      .then(({ invite }) => {
        if (cancelled || !invite || invite.status !== 'offered' || !invite.expires_at) return;
        setSignal({ inviteId: invite.invite_id, expiresAt: invite.expires_at });
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [connected]);

  // Realtime push from the world ticker when it offers a fresh invite.
  useEffect(() => {
    const socket = socketRef.current;
    if (!socket) return;
    const onInvite = (e: { invite_id: string; expires_at: string | null }) => {
      setSignal({ inviteId: e.invite_id, expiresAt: e.expires_at });
    };
    socket.on('main_event_invite', onInvite);
    return () => {
      socket.off('main_event_invite', onInvite);
    };
  }, [socketRef, connected]);

  return signal;
}

/** Game-page convenience: wire the invite signal into the countdown engine. */
export function useGameMainEventCountdown(params: UseMainEventInviteSignalParams): void {
  const { inviteId, expiresAt } = useMainEventInviteSignal(params);
  useCountdownToasts(expiresAt, inviteId);
}
