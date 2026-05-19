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
  aggression: number | null;     // baseline_aggression — bet/raise frequency
  looseness: number | null;      // baseline_looseness  — starting range width
  poise: number | null;          // poise               — tilt resistance
  expressiveness: number | null; // expressiveness      — readability at the table
  risk: number | null;           // risk_identity       — variance tolerance
}

export interface DossierPersonality {
  name: string | null;
  nickname: string | null;
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

export interface DossierResponse {
  personality_id: string;
  personality: DossierPersonality | null;
  emotion: string | null;
  observation: DossierObservation | null;
  pressure_summary: DossierPressureSummary | null;
  /** AI's off-table bankroll (chips), projected through regen.
   *  Null when no bankroll row exists yet (AI never sat down). */
  ai_bankroll: number | null;
  relationship: DossierRelationship | null;
  cash_pair_stats: DossierCashPairStats | null;
  memorable_hands: DossierMemorableHand[];
  note: string | null;
}

export async function fetchCharacterDossier(
  identifier: string,
): Promise<DossierResponse> {
  const res = await fetch(
    `${BASE}/${encodeURIComponent(identifier)}/dossier`,
    { credentials: 'include' },
  );
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || `HTTP ${res.status}`);
  }
  return res.json();
}

export async function saveCharacterNote(
  identifier: string,
  note: string,
): Promise<{ note: string | null }> {
  const res = await fetch(
    `${BASE}/${encodeURIComponent(identifier)}/note`,
    {
      method: 'PUT',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ note }),
    },
  );
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || `HTTP ${res.status}`);
  }
  return res.json();
}
