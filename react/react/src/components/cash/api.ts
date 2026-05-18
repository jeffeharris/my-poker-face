/**
 * REST client for /api/cash/* endpoints.
 *
 * Bare fetch wrapping — no SocketIO real-time push in v1. Components
 * call these methods in response to user actions and re-render from
 * the returned state. The polling interval (if any) is component-
 * driven, not built into this layer.
 */

import { config } from '../../config';
import type {
  CashAction,
  CashApiResponse,
  CashStateResponse,
  LobbyResponse,
  SitResponse,
  SitRequiresSponsor,
  SponsorOffer,
  SponsorOffersResponse,
  StakeLabel,
} from './types';

const BASE = `${config.API_URL}/api/cash`;

async function postJson<T>(path: string, body: object = {}): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || `HTTP ${res.status}`);
  }
  return res.json();
}

async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    credentials: 'include',
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    // Use a class that doesn't trigger the console.error for 4xx
    // responses we expect (e.g., 404 on /state when no session exists).
    const err = new Error(data.error || `HTTP ${res.status}`);
    (err as Error & { status?: number }).status = res.status;
    throw err;
  }
  return res.json();
}

export async function startCashSession(
  stakeLabel: StakeLabel,
  buyIn: number,
  seatIndex = 0,
): Promise<CashApiResponse & { game_id: string }> {
  return postJson('/start', {
    stake_label: stakeLabel,
    buy_in: buyIn,
    seat_index: seatIndex,
  });
}

export async function submitAction(
  action: CashAction,
  raiseTo = 0,
): Promise<CashApiResponse> {
  return postJson('/action', { action, raise_to: raiseTo });
}

export async function topUp(amount: number): Promise<{ stack: number; bankroll: number }> {
  return postJson('/topup', { amount });
}

export async function leaveTable(): Promise<CashApiResponse> {
  return postJson('/leave');
}

export async function getState(): Promise<CashStateResponse> {
  return getJson('/state');
}

export async function getSponsorOffers(
  stakeLabel: StakeLabel,
): Promise<SponsorOffersResponse> {
  return getJson(`/sponsor-offers?stake_label=${encodeURIComponent(stakeLabel)}`);
}

export async function sponsorAndSit(
  stakeLabel: StakeLabel,
  acceptor:
    | { archetype_id: string }
    | { lender_id: string },
): Promise<{ game_id: string; offer: SponsorOffer }> {
  return postJson('/sponsor-and-sit', {
    stake_label: stakeLabel,
    ...acceptor,
  });
}

export async function rebuy(amount: number): Promise<{ stack: number; bankroll: number }> {
  return postJson('/rebuy', { amount });
}

// --- Lobby v1.5 ---

export async function getLobby(): Promise<LobbyResponse> {
  return getJson('/lobby');
}

/**
 * Sit at a specific seat on a specific table.
 *
 * Returns the `SitResponse` on success. On 402 the server returned a
 * `SitRequiresSponsor` body — the caller should open SponsorModal.
 *
 * `buyIn` is optional; the server defaults to the table's min_buy_in.
 *
 * Throws on non-2xx, non-402 responses (network error / 404 / 409 etc.).
 */
export async function sitAtTable(
  tableId: string,
  seatIndex: number,
  buyIn?: number,
): Promise<SitResponse | { kind: 'requires_sponsor'; data: SitRequiresSponsor }> {
  const body: Record<string, unknown> = {
    table_id: tableId,
    seat_index: seatIndex,
  };
  if (typeof buyIn === 'number') {
    body.buy_in = buyIn;
  }
  const res = await fetch(`${config.API_URL}/api/cash/sit`, {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (res.status === 402) {
    const data = (await res.json()) as SitRequiresSponsor;
    return { kind: 'requires_sponsor', data };
  }
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error((data as { error?: string }).error || `HTTP ${res.status}`);
  }
  return (await res.json()) as SitResponse;
}
