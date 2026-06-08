/**
 * REST client for /api/tournament/* endpoints. Bare fetch, credentials
 * included (cookie auth) — mirrors components/cash/api.ts.
 */

import { config } from '../../config';
import type { TournamentLobbyResponse, TournamentStandings } from './types';

const BASE = `${config.API_URL}/api/tournament`;

async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { credentials: 'include' });
  if (!res.ok) throw new Error(`GET ${path} failed: ${res.status}`);
  return res.json();
}

async function postJson<T>(path: string, body: object = {}): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`POST ${path} failed: ${res.status}`);
  return res.json();
}

export const tournamentApi = {
  lobby: () => getJson<TournamentLobbyResponse>('/lobby'),
  // The on-demand `/register` route was removed in main (130ee314 — it seated the
  // human as a synthetic P01). Human Main Events now come from the economy-gated
  // invite flow: GET /invite opportunistically offers one (bank FLUSH + cooldown
  // + no active event), then POST /invite/accept spawns the real-persona field.
  getInvite: () => getJson<{ invite: unknown | null }>('/invite'),
  acceptInvite: () => postJson<{ tournament_id: string }>('/invite/accept'),
  /** Build (or return) the human's LIVE single-table game; navigate to /game/:id. */
  sit: (id: string) => postJson<{ game_id: string }>(`/${id}/sit`),
  standings: (id: string) => getJson<TournamentStandings>(`/${id}/standings`),
  advance: (id: string) => postJson<TournamentStandings>(`/${id}/advance`),
  playOut: (id: string) => postJson<TournamentStandings>(`/${id}/play-out`),
  leave: (id: string) => fetch(`${BASE}/${id}`, { method: 'DELETE', credentials: 'include' }),
};
