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
  CashSessionState,
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
    throw new Error(data.error || `HTTP ${res.status}`);
  }
  return res.json();
}

export async function startCashSession(
  stakeLabel: StakeLabel,
  buyIn: number,
  seatIndex = 0,
): Promise<CashApiResponse> {
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

export async function topUp(amount: number): Promise<{ state: CashSessionState }> {
  return postJson('/topup', { amount });
}

export async function leaveTable(): Promise<CashApiResponse> {
  return postJson('/leave');
}

export async function getState(): Promise<{ state: CashSessionState }> {
  return getJson('/state');
}
