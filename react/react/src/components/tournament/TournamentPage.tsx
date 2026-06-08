/**
 * Container for the multi-table tournament experience, mirroring the cash/circuit
 * join flow: register (pick field) → sit → drop into the live poker table at
 * /game/:id. The standings screen is the hub you back out to (player-gated time —
 * the whole field is paused while you read it): Return to Table to keep playing,
 * Watch to spectate after a bust, or Leave.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { createAuthedSocket } from '../../utils/socket';
import { config } from '../../config';
import { tournamentApi } from './api';
import { TournamentLobby } from './TournamentLobby';
import { TournamentStandings } from './TournamentStandings';
import type {
  RegisterRequest,
  TournamentLobbyActive,
  TournamentStandings as Standings,
} from './types';
import './tournament.css';

// Match useSocket.ts: pin dev to long-polling (the Werkzeug + threading dev
// server mishandles the WS upgrade); let socket.io negotiate in prod.
const SOCKET_TRANSPORTS = import.meta.env.PROD ? undefined : ['polling'];

type View = 'loading' | 'lobby' | 'standings';

export function TournamentPage() {
  const navigate = useNavigate();
  const [view, setView] = useState<View>('loading');
  const [tournamentId, setTournamentId] = useState<string | null>(null);
  const [standings, setStandings] = useState<Standings | null>(null);
  const [active, setActive] = useState<TournamentLobbyActive | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadLobby = useCallback(async () => {
    try {
      const lobby = await tournamentApi.lobby();
      if (lobby.active) {
        // An event is in progress — show it as the standings hub.
        setTournamentId(lobby.active.tournament_id);
        setStandings(lobby.active.standings);
        setActive(lobby.active);
        setView('standings');
      } else {
        setActive(null);
        setView('lobby');
      }
    } catch {
      setView('lobby');
    }
  }, []);

  useEffect(() => {
    loadLobby();
  }, [loadLobby]);

  /** Build (or resume) the live table and drop into it. */
  const goToTable = useCallback(
    async (id: string) => {
      setBusy(true);
      setError(null);
      try {
        const { game_id } = await tournamentApi.sit(id);
        navigate(`/game/${game_id}`);
      } catch {
        setError('Could not open your table.');
        setBusy(false);
      }
    },
    [navigate]
  );

  const handleRegister = async (body: RegisterRequest) => {
    setBusy(true);
    setError(null);
    try {
      const res = await tournamentApi.register(body);
      setTournamentId(res.tournament_id);
      await goToTable(res.tournament_id); // straight to the felt
    } catch {
      setError('Could not register — you may already be in an event.');
      setBusy(false);
    }
  };

  const refreshStandings = useCallback(async () => {
    if (!tournamentId) return;
    try {
      setStandings(await tournamentApi.standings(tournamentId));
    } catch {
      /* keep last */
    }
  }, [tournamentId]);

  // Keep the latest refresh in a ref so the socket effect can stay mount-only
  // (re-subscribing on every tournamentId change would churn the connection).
  const refreshStandingsRef = useRef(refreshStandings);
  refreshStandingsRef.current = refreshStandings;

  // Realtime: the backend pushes `mtt_update` to the owner's lobby room on each
  // field advance. The field is player-gated (it only moves during the human's
  // hands at the live table), so on the hub this mainly keeps standings fresh
  // while spectating a play-out. Mirrors the cash Lobby socket pattern; no-op if
  // the socket can't connect (backing out re-fetches on view change anyway).
  useEffect(() => {
    const socket = createAuthedSocket(config.SOCKET_URL, {
      withCredentials: true,
      ...(SOCKET_TRANSPORTS ? { transports: SOCKET_TRANSPORTS } : {}),
    });
    const onUpdate = () => void refreshStandingsRef.current();
    socket.on('mtt_update', onUpdate);
    return () => {
      socket.off('mtt_update', onUpdate);
      socket.disconnect();
    };
  }, []);

  const handleWatch = async () => {
    if (!tournamentId) return;
    setBusy(true);
    try {
      setStandings(await tournamentApi.playOut(tournamentId));
    } finally {
      setBusy(false);
    }
  };

  const handleLeave = async () => {
    if (tournamentId) {
      try {
        await tournamentApi.leave(tournamentId);
      } catch {
        /* best-effort */
      }
    }
    setTournamentId(null);
    setStandings(null);
    setActive(null);
    await loadLobby();
  };

  // Refresh standings whenever we land on the hub (e.g. backing out of a hand).
  useEffect(() => {
    if (view === 'standings') refreshStandings();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view]);

  if (view === 'loading') {
    return (
      <div className="tourney">
        <div className="tlobby__state">Reading the room…</div>
      </div>
    );
  }

  if (view === 'standings' && standings) {
    return (
      <TournamentStandings
        standings={standings}
        busy={busy}
        onReturnToTable={() => tournamentId && goToTable(tournamentId)}
        onWatch={handleWatch}
        onLeave={handleLeave}
        onBack={() => navigate('/menu')}
      />
    );
  }

  return (
    <div>
      <button
        className="tourney__back"
        style={{ position: 'fixed', top: 16, left: 16, zIndex: 5 }}
        onClick={() => navigate('/menu')}
      >
        ‹ Menu
      </button>
      <TournamentLobby
        active={active}
        busy={busy}
        error={error}
        onRegister={handleRegister}
        onResume={() => active && goToTable(active.tournament_id)}
      />
    </div>
  );
}

export default TournamentPage;
