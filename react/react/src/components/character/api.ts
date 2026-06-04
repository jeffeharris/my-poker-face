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

/** Tier-2 deep postflop reads (B1) — the long-grind unlocks surfaced past
 *  ~180 hands. Each field is null until its grind tier (or the informant's
 *  "Deep postflop read" section) is unlocked, OR until enough of that
 *  opportunity has been observed to compute it. Rates are 0–1; the equity
 *  fields are mean win-prob (0–1) the opponent held at that action. */
export interface DossierDeeperReads {
  fold_to_cbet: number | null;
  cbet_attempt_rate: number | null;
  barrel_frequency: number | null;
  third_barrel_frequency: number | null;
  all_in_frequency: number | null;
  aggression_factor_postflop: number | null;
  equity_when_betting: number | null;
  equity_when_raising: number | null;
  equity_when_calling: number | null;
  lifetime?: boolean;
}

/** B2 "the read" — one piece of exploit advice from the tiered-bot
 *  exploitation detectors. `intensity` (0–1, or null) is how strongly the
 *  pattern matches, for phrasing emphasis. */
export interface DossierReadTip {
  pattern: string;
  text: string;
  intensity: number | null;
}

/** Coarse opponent archetype badge (B2). Null until the archetype tier is
 *  unlocked, or when the opponent doesn't match a surfaced archetype. */
export interface DossierArchetype {
  id: string;
  label: string;
}

/** B3 emotional read — how they handle pressure. `tilt_score`/`tilt_label`
 *  present only with enough pressure history; `poise` (tilt resistance) and
 *  `expressiveness` (readability) are 0–1 personality anchors. `lines` are the
 *  one-line tells. */
export interface DossierTemperament {
  tilt_score: number | null;
  tilt_label: string | null;
  poise: number | null;
  expressiveness: number | null;
  lines: string[];
}

/** B4 — where the opponent sits in the LLM field for VPIP / aggression. */
export interface DossierFieldPosition {
  vpip_pct?: number;
  vpip_label?: string;
  af_pct?: number;
  af_label?: string;
}

/** B1 (Renown v2) — this AI's field-relative renown standing in the sandbox.
 *  Null until the per-AI renown persist path has run (RENOWN_V2_PERSIST_AI on,
 *  migration applied, the ticker has scored the field). Read-only. */
export interface DossierReputation {
  formula_version: 'v2';
  /** "Beloved Legend" | "Infamous Villain" | "Up-and-comer" | "Disliked Nobody" */
  quadrant: string;
  /** Uncapped lifetime renown points (own-scale ratchet). */
  renown_v2: number;
  /** This AI's renown percentile across the field [0,1]. */
  victim_percentile: number | null;
  /** Field-wide "high renown" cut at capture time (gap to "figure"). */
  high_cut: number | null;
  /** Entities scored that cycle. */
  field_size: number | null;
}

/** One relationship-event tally (clash or banter). */
export interface DossierHistoryEvent {
  event: string;
  label: string;
  count: number;
}

/** "The history" — the rivalry read between the human and this opponent. */
export interface DossierRelationshipHistory {
  line: string;
  defining: {
    event: string;
    label: string;
    impact_score: number;
    narrative: string;
  } | null;
  clash: DossierHistoryEvent[];
  banter: DossierHistoryEvent[];
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
  /** Hand-count floor for this read (always present). */
  unlocks_at: number;
  /** Tier-2 opportunity gate (present only for sample-gated reads): the read
   *  also needs `samples_observed` to reach `sample_min` of `sample_noun`
   *  (e.g. "c-bets faced") before it unlocks. */
  sample_min?: number;
  samples_observed?: number;
  sample_noun?: string;
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

/** Credit-history (bankruptcy record) section — a STANDALONE unlock, not
 *  part of the hand-count scouting grind (bankruptcy is financial
 *  reputation, not a poker tell). `revealed` is true when the viewer has
 *  first-hand exposure (this borrower defaulted on a stake from them) or
 *  bought the report from the informant. When locked, `unlock` carries the
 *  section id + price so the client can offer the buy. */
export type DossierCreditHistory =
  | {
      revealed: true;
      source: 'first_hand' | 'informant';
      bankruptcy_count: number;
      last_bankruptcy_at: string | null;
      recently_bankrupt: boolean;
    }
  | {
      revealed: false;
      unlock: { section_id: string; price: number };
    };

export interface DossierResponse {
  personality_id: string;
  personality: DossierPersonality | null;
  emotion: string | null;
  observation: DossierObservation | null;
  /** Tier-2 deep postflop reads. Null when ungated/no data; individual
   *  fields null when their grind tier is still locked. */
  deeper_reads?: DossierDeeperReads | null;
  /** B2 "the read": exploit advice lines. Empty when locked/no read. */
  the_read?: DossierReadTip[];
  /** B2 archetype badge. Null when locked or unmatched. */
  archetype?: DossierArchetype | null;
  /** B3 emotional read (tilt / poise / readability). Null when locked. */
  temperament?: DossierTemperament | null;
  /** B4 field-relative standing. Null when locked. */
  field_position?: DossierFieldPosition | null;
  /** "The history" — rivalry read. Null when locked / no shared history. */
  relationship_history?: DossierRelationshipHistory | null;
  pressure_summary: DossierPressureSummary | null;
  /** AI's off-table bankroll (chips), projected through regen.
   *  Null when no bankroll row exists yet (AI never sat down). */
  ai_bankroll: number | null;
  /** Outstanding-carry totals (both directions). Defaults to all
   *  zeros when the AI has no stake history. */
  stake_summary: DossierStakeSummary;
  /** Bankruptcy credit history. Null when ungated/no-sandbox/anonymous;
   *  otherwise revealed (first-hand or bought) or a locked teaser. */
  credit_history?: DossierCreditHistory | null;
  relationship: DossierRelationship | null;
  cash_pair_stats: DossierCashPairStats | null;
  memorable_hands: DossierMemorableHand[];
  note: string | null;
  /** B1 (Renown v2) AI standing badge. Null when not yet persisted. */
  reputation?: DossierReputation | null;
  /** Scouting gate state (Phase 2). Null/absent when the dossier is
   *  ungated (no Circuit sandbox context). */
  scouting?: DossierScouting | null;
  /** The viewer's own bankroll (chips) — lets the informant UI disable
   *  unlocks they can't afford. Null when no bankroll row / no observer. */
  player_bankroll?: number | null;
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
): Promise<{
  scouting?: DossierScouting;
  bankroll: number;
  section_id: string;
  price: number;
}> {
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
