import { useEffect, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import type { Socket } from 'socket.io-client';
import toast from 'react-hot-toast';
import { isTournamentGameId } from '../utils/gameId';
import { getOrdinal } from '../types/tournament';
import type {
  MttCompleteEvent,
  MttEliminatedEvent,
  MttRelocatedEvent,
} from '../components/tournament/types';

interface UseTournamentEventsParams {
  /** The game-page socket (owned by usePokerGame). May be null until connected. */
  socketRef: React.MutableRefObject<Socket | null>;
  /** usePokerGame's connection flag — re-attach listeners when this flips true. */
  connected: boolean;
  /** Current game id; the hook is a no-op unless this is a `tourney-` table. */
  gameId: string | null;
}

/**
 * After the bust/win hand, let the showdown + winner announcement play before
 * we route away to the standings hub. The MTT terminal event arrives at the
 * hand boundary, right after the winner announcement is emitted — navigating
 * instantly would cut off the reveal of the hand that ended the run.
 */
const TERMINAL_NAV_DELAY_MS = 3500;

/**
 * useTournamentEvents — the game-page consumer of the multi-table-tournament
 * (`mtt_*`) realtime events. Sibling to the other game-page socket consumers
 * (useRunoutDirector etc.); the events themselves are documented in
 * components/tournament/types.ts and emitted by
 * flask_app/handlers/tournament_game_builder.py `_emit_tournament`.
 *
 * The human plays their seat as an ordinary game at /game/:id while the rest of
 * the field advances headless. These events are how the meta-layer reaches the
 * felt:
 *   - `mtt_relocated`  → a "you've been moved" toast (the live table is
 *                        reconciled in place; play continues).
 *   - `mtt_eliminated` → busted: toast the finish, then route to the standings
 *                        hub (which shows the busted rank + Watch/Leave).
 *   - `mtt_complete`   → field done: toast win/finish, then route to the hub
 *                        (which shows the champion band).
 *
 * The game socket is joined to the owner's lobby room on connect, so these
 * lobby-room emits arrive here. Deliberately distinct from the legacy
 * single-table `tournament_complete` (consumed by usePokerGame into the
 * `TournamentResult` overlay) — different payload, different screen.
 */
export function useTournamentEvents({
  socketRef,
  connected,
  gameId,
}: UseTournamentEventsParams): void {
  const navigate = useNavigate();
  const navTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    const socket = socketRef.current;
    if (!socket || !connected || !isTournamentGameId(gameId)) return;

    const onRelocated = (e: MttRelocatedEvent) => {
      toast(`You've been moved to Table ${e.table_id}`, { icon: '🪑' });
    };

    // Route to the standings hub after the ending hand's reveal has played.
    const routeToHub = () => {
      if (navTimerRef.current) return; // a terminal beat already scheduled
      navTimerRef.current = setTimeout(() => {
        navTimerRef.current = null;
        navigate('/tournament');
      }, TERMINAL_NAV_DELAY_MS);
    };

    const onEliminated = (e: MttEliminatedEvent) => {
      const place = e.finishing_position != null ? getOrdinal(e.finishing_position) : null;
      toast(place ? `Eliminated — you finished ${place}` : 'Eliminated', { icon: '☠️' });
      routeToHub();
    };

    const onComplete = (e: MttCompleteEvent) => {
      const human = e.standings?.human;
      const wonIt = human != null && e.standings?.winner === human.player_id;
      if (wonIt) {
        toast.success('🏆 You won the tournament!', { duration: 6000 });
      } else {
        const place = human?.rank != null ? getOrdinal(human.rank) : null;
        toast(place ? `Tournament over — you finished ${place}` : 'Tournament over');
      }
      // Do NOT route to the hub here: when the field completes with the human
      // still at the table, the backend also emits `tournament_complete`, which
      // usePokerGame renders as the shared TournamentComplete screen (the same
      // end screen single-table games use). Its "Return to Menu" button is the
      // exit. Early busts route to the hub via `mtt_eliminated` (onEliminated).
    };

    socket.on('mtt_relocated', onRelocated);
    socket.on('mtt_eliminated', onEliminated);
    socket.on('mtt_complete', onComplete);

    return () => {
      socket.off('mtt_relocated', onRelocated);
      socket.off('mtt_eliminated', onEliminated);
      socket.off('mtt_complete', onComplete);
    };
  }, [socketRef, connected, gameId, navigate]);

  // Clear any pending terminal navigation if we unmount first.
  useEffect(() => {
    return () => {
      if (navTimerRef.current) clearTimeout(navTimerRef.current);
    };
  }, []);
}
