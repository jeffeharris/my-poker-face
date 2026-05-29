/**
 * REST client for /api/character/* — dossier read + note save.
 *
 * Identifier may be a personality_id (lobby seats carry it) or a
 * display name (the React Player blob exposes name, not id). The
 * backend tries id first, then resolves name → id.
 */

import { config } from '../../config';

const BASE = `${config.API_URL}/api/character`;

export interface DossierRelationship {
  heat: number;
  respect: number;
  likability: number;
  last_seen: string | null;
  hint: string;
}

export interface DossierCashPairStats {
  cumulative_pnl: number;
  hands_played_cash: number;
}

/** Player-facing subset of the AI's psychology anchors (5 of 9
 *  axes — the ones that meaningfully shape what shows across the
 *  table). The other anchors (ego, recovery_rate, etc.) are
 *  internal plumbing for tilt dynamics and don't add player signal. */
export interface DossierAnchors {
  aggression: number | null; // baseline_aggression — bet/raise frequency
  looseness: number | null; // baseline_looseness  — starting range width
  poise: number | null; // poise               — tilt resistance
  expressiveness: number | null; // expressiveness      — readability at the table
  risk: number | null; // risk_identity       — variance tolerance
}

export interface DossierPersonality {
  name: string | null;
  /** Displayed alias — the viewer's private override when present,
   *  otherwise the personality's canonical nickname. */
  nickname: string | null;
  /** The original nickname from personalities.json, ignoring any
   *  per-viewer override. Surfaced so the editor can show what the
   *  override is replacing. */
  canonical_nickname: string | null;
  /** Raw stored override; null when the viewer hasn't set one. */
  nickname_override: string | null;
  play_style: string | null;
  attitude: string | null;
  confidence: string | null;
  signature_line: string | null;
  anchors: DossierAnchors | null;
}

export interface DossierObservation {
  hands_observed: number;
  vpip: number;
  pfr: number;
  aggression_factor: number;
  play_style: string;
}

export interface DossierPressureSummary {
  total_events?: number;
  wins?: number;
  big_wins?: number;
  big_losses?: number;
  successful_bluffs?: number;
  bluffs_caught?: number;
  bad_beats?: number;
  eliminations?: number;
  biggest_pot_won?: number;
  biggest_pot_lost?: number;
  tilt_score?: number;
  aggression_score?: number;
  signature_move?: string;
  headsup_wins?: number;
  headsup_losses?: number;
}

export interface DossierMemorableHand {
  hand_id: number;
  event: string;
  impact_score: number;
  narrative: string;
  hand_summary: string;
  timestamp: string | null;
}

/** Stake-position summary surfaced on the dossier (Phase 4 of the
 *  backing system). Counts and total chip amounts for outstanding
 *  carries this AI is involved in, both as borrower and as staker.
 *  Active (in-session) stakes are NOT counted here — only carries
 *  (residual debts that survived a session bust). */
export interface DossierStakeSummary {
  as_borrower: {
    carry_count: number;
    total_carried: number;
  };
  as_staker: {
    carry_count: number;
    total_owed_to_them: number;
  };
}

/** Phase 2 scouting gate state. Present only in a Circuit context (a
 *  sandbox + observer); absent when the dossier is ungated. Earnable reads
 *  are stripped from the payload until unlocked — this descriptor tells the
 *  client what's locked and the progress toward each unlock. */
export interface DossierScoutingLock {
  id: string;
  label: string;
  unlocks_at: number;
}

/** A still-buyable informant section (Phase 3): pay `price` chips to
 *  reveal it. */
export interface DossierInformantOffer {
  id: string;
  label: string;
  price: number;
}

export interface DossierScouting {
  hands_observed: number;
  floor: number;
  floor_met: boolean;
  unlocked: string[];
  locked: DossierScoutingLock[];
  informant_offers?: DossierInformantOffer[];
}

export interface DossierResponse {
  personality_id: string;
  personality: DossierPersonality | null;
  emotion: string | null;
  observation: DossierObservation | null;
  pressure_summary: DossierPressureSummary | null;
  /** AI's off-table bankroll (chips), projected through regen.
   *  Null when no bankroll row exists yet (AI never sat down). */
  ai_bankroll: number | null;
  /** Outstanding-carry totals (both directions). Defaults to all
   *  zeros when the AI has no stake history. */
  stake_summary: DossierStakeSummary;
  relationship: DossierRelationship | null;
  cash_pair_stats: DossierCashPairStats | null;
  memorable_hands: DossierMemorableHand[];
  note: string | null;
  /** Scouting gate state (Phase 2). Null/absent when the dossier is
   *  ungated (no Circuit sandbox context). */
  scouting?: DossierScouting | null;
}

/**
 * Bulk-load every nickname override the current viewer has set.
 * Keyed by personality display name so the client can look up
 * against `player.name` directly. Anonymous callers get `{}`.
 */
export async function fetchNicknameOverrides(): Promise<Record<string, string>> {
  const res = await fetch(`${BASE}/nickname-overrides`, {
    credentials: 'include',
  });
  if (!res.ok) {
    throw new Error(`HTTP ${res.status}`);
  }
  const data = await res.json();
  return data?.overrides ?? {};
}

export async function fetchCharacterDossier(identifier: string): Promise<DossierResponse> {
  const res = await fetch(`${BASE}/${encodeURIComponent(identifier)}/dossier`, {
    credentials: 'include',
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || `HTTP ${res.status}`);
  }
  return res.json();
}

/** Pay the informant to reveal a still-locked dossier section (Phase 3).
 *  Returns the updated scouting state + the player's new bankroll. Throws
 *  with the server's error message on 4xx (e.g. insufficient bankroll). */
export async function buyInformantUnlock(
  identifier: string,
  sectionId: string
): Promise<{ scouting: DossierScouting; bankroll: number; section_id: string; price: number }> {
  const res = await fetch(`${BASE}/${encodeURIComponent(identifier)}/informant`, {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ section_id: sectionId }),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || `HTTP ${res.status}`);
  }
  return res.json();
}

export async function saveCharacterNote(
  identifier: string,
  note: string
): Promise<{ note: string | null }> {
  const res = await fetch(`${BASE}/${encodeURIComponent(identifier)}/note`, {
    method: 'PUT',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ note }),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || `HTTP ${res.status}`);
  }
  return res.json();
}

/**
 * Save a per-viewer nickname override. An empty string clears the
 * override; the dossier then falls back to the canonical nickname.
 * Server caps length at 60 chars (see NICKNAME_OVERRIDE_MAX_LEN).
 */
export async function saveCharacterNicknameOverride(
  identifier: string,
  nickname: string
): Promise<{ nickname_override: string | null }> {
  const res = await fetch(`${BASE}/${encodeURIComponent(identifier)}/nickname`, {
    method: 'PUT',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ nickname }),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || `HTTP ${res.status}`);
  }
  return res.json();
}

export const NICKNAME_OVERRIDE_MAX_LEN = 60;
