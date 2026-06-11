/**
 * REST client for /api/tournament/* endpoints. Bare fetch, credentials
 * included (cookie auth) — mirrors components/cash/api.ts.
 */

import { config } from '../../config';
import type {
  RegisterRequest,
  RegisterResponse,
  TournamentLobbyResponse,
  TournamentStandings,
} from './types';

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
  /** Start an on-demand, fully-isolated exhibition ("decoupled") Main Event. */
  spawn: (body: RegisterRequest) => postJson<RegisterResponse>('/spawn', body),
  /** Build (or return) the human's LIVE single-table game; navigate to /game/:id. */
  sit: (id: string) => postJson<{ game_id: string }>(`/${id}/sit`),
  standings: (id: string) => getJson<TournamentStandings>(`/${id}/standings`),
  advance: (id: string) => postJson<TournamentStandings>(`/${id}/advance`),
  playOut: (id: string) => postJson<TournamentStandings>(`/${id}/play-out`),
  leave: (id: string) => fetch(`${BASE}/${id}`, { method: 'DELETE', credentials: 'include' }),
};
