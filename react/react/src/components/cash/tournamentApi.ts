/**
 * REST client for the circuit Main Event (`/api/tournament/*`).
 *
 * The player's one decision is the **invite** — Register (play it) or Decline
 * (it runs autonomously). These wrap the invite lifecycle + the live-table
 * bridge the Lobby's Main Event card drives. Separate from `api.ts` because the
 * tournament routes live outside `/api/cash`. See
 * `docs/plans/P3_REMAINING_HANDOFF.md` §P3.8.
 */

import { config } from '../../config';

const BASE = `${config.API_URL}/api/tournament`;

/** The open Main Event invite shown on the lobby card (trimmed server-side). */
export interface TournamentInvite {
  invite_id: string;
  status: string;
  buy_in: number;
  field_size: number;
  table_size: number;
  starting_stack: number;
  /** ISO-8601 expiry, or null when the offer has no auto-expiry window. */
  expires_at: string | null;
}

export interface InviteResponse {
  invite: TournamentInvite | null;
}

/** Raised when accepting a buy-in Main Event the player can't cover (HTTP 402).
 *  Carries the amounts so the card can show "need X, have Y". */
export class InsufficientFundsError extends Error {
  required: number;
  available: number;
  constructor(required: number, available: number) {
    super('insufficient_funds');
    this.name = 'InsufficientFundsError';
    this.required = required;
    this.available = available;
  }
}

async function postJson<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: '{}',
  });
  if (res.status === 402) {
    const data = (await res.json().catch(() => ({}))) as {
      required?: number;
      available?: number;
    };
    throw new InsufficientFundsError(data.required ?? 0, data.available ?? 0);
  }
  if (!res.ok) {
    const data = (await res.json().catch(() => ({}))) as { error?: string; message?: string };
    // Prefer a human-readable `message` (e.g. cannot_field_tournament's "not
    // enough players available right now") over the machine `error` code.
    throw new Error(data.message || data.error || `HTTP ${res.status}`);
  }
  return res.json();
}

/** The owner's open Main Event invite, or `{ invite: null }`. The GET also
 *  opportunistically lets the chairman offer / expire invites server-side, so
 *  polling it on lobby load (and on `lobby_tick`) keeps the card fresh without a
 *  background scheduler. */
export async function getInvite(): Promise<InviteResponse> {
  const res = await fetch(`${BASE}/invite`, { credentials: 'include' });
  if (!res.ok) {
    const data = (await res.json().catch(() => ({}))) as { error?: string; message?: string };
    // Prefer a human-readable `message` (e.g. cannot_field_tournament's "not
    // enough players available right now") over the machine `error` code.
    throw new Error(data.message || data.error || `HTTP ${res.status}`);
  }
  return res.json();
}

export interface TournamentLobby {
  has_active: boolean;
  active: { tournament_id: string; created_at: string } | null;
}

/** Whether the owner has a tournament they're currently playing IN, so the lobby
 *  can show a "Resume Main Event" bar (the offer card vanishes once accepted, so
 *  without this there's no way back to an in-progress Main Event). */
export async function getTournamentLobby(): Promise<TournamentLobby> {
  const res = await fetch(`${BASE}/lobby`, { credentials: 'include' });
  if (!res.ok) {
    const data = (await res.json().catch(() => ({}))) as { error?: string };
    throw new Error(data.error || `HTTP ${res.status}`);
  }
  return res.json();
}

/** Accept the open invite → builds the tournament the human plays IN (and
 *  stands them up from any cash seat first). Throws `InsufficientFundsError` on
 *  402 when a buy-in can't be covered. */
export async function acceptInvite(): Promise<{ tournament_id: string }> {
  return postJson('/invite/accept');
}

/** Decline → the Main Event starts autonomously (AI-only). */
export async function declineInvite(): Promise<{ ok: boolean; tournament_id: string }> {
  return postJson('/invite/decline');
}

/** Build (or return) the human's live single-table game for the tournament so
 *  they can play it through the normal game UI. Call right after accept. */
export async function sitTournament(tournamentId: string): Promise<{ game_id: string }> {
  return postJson(`/${encodeURIComponent(tournamentId)}/sit`);
}
