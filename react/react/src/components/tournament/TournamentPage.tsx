/**
 * Container for the tournament experience: resolves the lobby, then shows either
 * the register/resume entry or the live standings. Owns all /api/tournament/*
 * calls and the (interim) advance / fast-forward controls.
 *
 * Until the live game-handler bridge (Phase 2c), advancing simulates the human's
 * table along with the AI tables, so the standings screen has real, evolving data.
 */

import { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { tournamentApi } from './api';
import { TournamentLobby } from './TournamentLobby';
import { TournamentStandings } from './TournamentStandings';
import type { RegisterRequest, TournamentLobbyActive, TournamentStandings as Standings } from './types';
import './tournament.css';

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
      setActive(lobby.active);
      setView('lobby');
    } catch {
      setView('lobby');
    }
  }, []);

  useEffect(() => {
    loadLobby();
  }, [loadLobby]);

  const enter = (id: string, s: Standings) => {
    setTournamentId(id);
    setStandings(s);
    setView('standings');
    setError(null);
  };

  const handleRegister = async (body: RegisterRequest) => {
    setBusy(true);
    setError(null);
    try {
      const res = await tournamentApi.register(body);
      enter(res.tournament_id, res.standings);
    } catch {
      setError('Could not register — you may already be in an event.');
    } finally {
      setBusy(false);
    }
  };

  const handleResume = async () => {
    if (!active) return;
    enter(active.tournament_id, active.standings);
  };

  const runStep = async (fn: (id: string) => Promise<Standings>) => {
    if (!tournamentId) return;
    setBusy(true);
    try {
      setStandings(await fn(tournamentId));
    } catch {
      setError('Something went wrong advancing the tournament.');
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
        onAdvance={() => runStep(tournamentApi.advance)}
        onPlayOut={() => runStep(tournamentApi.playOut)}
        onLeave={handleLeave}
        onBack={() => setView('lobby')}
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
        onResume={handleResume}
      />
    </div>
  );
}

export default TournamentPage;
