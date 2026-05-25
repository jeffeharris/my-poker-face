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
  ForgivenessRateLimited,
  ForgivenessRequestsResponse,
  ForgivenessResponse,
  LobbyResponse,
  NetWorthResponse,
  PayoffResponse,
  SitResponse,
  SitRequiresSponsor,
  SponsorOffer,
  SponsorOffersResponse,
  StakableAiResponse,
  StakeFormat,
  StakeLabel,
  StakeOfferResponse,
  StakerForgiveResponse,
  WorldPace,
} from './types';

const BASE = `${config.API_URL}/api/cash`;

/** POST without throwing on non-2xx — caller branches on status. Used
 *  when the route's "error" responses (like 429) carry information
 *  the caller wants to act on rather than surface as a generic Error. */
async function postJsonRaw(
  path: string, body: object = {},
): Promise<Response> {
  return fetch(`${BASE}${path}`, {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

async function postJson<T>(path: string, body: object = {}): Promise<T> {
  const res = await postJsonRaw(path, body);
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
  // Lobby v1.5: when the sponsor flow originated from a specific seat
  // tap, pass the table identity so the game is built against the AIs
  // the lobby showed. Omitting both falls back to the legacy fresh-
  // sample path (sponsor flow opened from a stake card with no seat
  // context).
  origin?: { table_id: string; seat_index: number },
): Promise<{ game_id: string; offer: SponsorOffer }> {
  return postJson('/sponsor-and-sit', {
    stake_label: stakeLabel,
    ...acceptor,
    ...(origin ?? {}),
  });
}

export async function rebuy(amount: number): Promise<{ stack: number; bankroll: number }> {
  return postJson('/rebuy', { amount });
}

// --- Lobby v1.5 ---

export async function getLobby(): Promise<LobbyResponse> {
  return getJson('/lobby');
}

/** Set how fast the background world ticks (subtle/lively/bustling).
 *  Persisted per user; the realtime ticker picks it up next cycle. */
export async function setWorldPace(pace: WorldPace): Promise<{ world_pace: WorldPace }> {
  const res = await fetch(`${BASE}/world-pace`, {
    method: 'PUT',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ pace }),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error((data as { error?: string }).error || `HTTP ${res.status}`);
  }
  return res.json();
}

// --- Net Worth (Phase 3) ---

export async function getNetWorth(): Promise<NetWorthResponse> {
  return getJson('/net-worth');
}

export async function payOffCarry(stakeId: string): Promise<PayoffResponse> {
  return postJson(`/stakes/${encodeURIComponent(stakeId)}/payoff`);
}

/** Request forgiveness on an outstanding carry. The server reads the
 *  staker's view of the borrower (likability/respect/heat) and either
 *  clears the carry (granted) or refuses (carry stays). The 429 path
 *  is distinguished from a normal Error so the caller can surface a
 *  countdown rather than a generic message. */
export async function requestForgiveness(
  stakeId: string,
): Promise<
  | { kind: 'decided'; data: ForgivenessResponse }
  | { kind: 'rate_limited'; data: ForgivenessRateLimited }
> {
  const res = await postJsonRaw(
    `/stakes/${encodeURIComponent(stakeId)}/request-forgiveness`,
  );
  if (res.status === 429) {
    return {
      kind: 'rate_limited',
      data: (await res.json()) as ForgivenessRateLimited,
    };
  }
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error((data as { error?: string }).error || `HTTP ${res.status}`);
  }
  return { kind: 'decided', data: (await res.json()) as ForgivenessResponse };
}

// --- v110: AI-to-player forgiveness consent flow ---

/** Fetch every AI-initiated forgiveness request waiting on this
 *  player's decision. Drives the Forgiveness Requests section in
 *  the Net Worth Drawer; the wallet badge count comes from
 *  /net-worth's `pending_forgiveness_count` to avoid a second round
 *  trip per refresh. */
export async function getForgivenessRequests(): Promise<ForgivenessRequestsResponse> {
  return getJson('/forgiveness-requests');
}

/** Grant or refuse a pending forgiveness ask. On grant the carry
 *  clears and the AI's relationship axes register STAKE_FORGIVEN
 *  (warmer); on refuse the ask clears and STAKE_FORGIVENESS_REFUSED
 *  fires (cooler). Either way the badge clears for this stake. */
export async function stakerForgive(
  stakeId: string,
  grant: boolean,
): Promise<StakerForgiveResponse> {
  return postJson(
    `/stakes/${encodeURIComponent(stakeId)}/staker-forgive`,
    { grant },
  );
}

// --- Phase 5: Player as staker ---

/** Fetch the curated per-tier list of AIs the player can offer a
 *  stake to right now. Server runs every gate (cash-eligible, willing,
 *  met-before, relationship floor, +1 tier rule, cooldown, etc.) so
 *  what comes back is what the player can act on. Empty `by_tier`
 *  means "no one's ready right now." */
export async function getStakableAi(): Promise<StakableAiResponse> {
  return getJson('/stakable-ai');
}

/** Offer a stake to a specific AI. The route validates the structural
 *  gates (bankroll, +1 tier, met-before, etc.) and then evaluates the
 *  AI's willingness against the SPECIFIC offer terms (cut, format,
 *  desperation). The `accepted` field distinguishes a successful
 *  sit-down from a polite refusal — both are 200 (only client-error
 *  rejections like an invalid stake_label produce non-2xx). */
export async function offerStake(args: {
  targetPid: string;
  stakeLabel: StakeLabel;
  principal: number;
  cut: number;
  format?: StakeFormat;
  matchAmount?: number;
  originationFee?: number;
}): Promise<StakeOfferResponse> {
  const body: Record<string, unknown> = {
    target_pid: args.targetPid,
    stake_label: args.stakeLabel,
    principal: args.principal,
    cut: args.cut,
  };
  if (args.format) body.format = args.format;
  if (args.matchAmount !== undefined) body.match_amount = args.matchAmount;
  if (args.originationFee !== undefined) body.origination_fee = args.originationFee;
  return postJson('/stakes/offer', body);
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
