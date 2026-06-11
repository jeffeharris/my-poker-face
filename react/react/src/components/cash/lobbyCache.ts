/**
 * Stale-while-revalidate cache for the cash lobby ("The Circuit").
 *
 * Opening the lobby blocks its hero/reputation/tables on the `getLobby()` fetch,
 * so the first paint is empty until the round-trip lands. We persist a snapshot
 * of the *inert display* fields per user and seed them before paint, so the
 * lobby shows last-known state instantly and the live fetch overwrites it.
 *
 * Deliberately NOT cached: one-shot / sticky / server-cleared fields —
 * `intake_needed`, `intake_backstories`, `mentor_intro`, `mentor_stake`, and the
 * merged `events` feed. Those must come from server truth only, or a stale cache
 * would re-fire onboarding/mentor beats. Keep this snapshot side-effect-free.
 */

import type { BankrollPoint, LobbyTable, ReputationData, WorldPace } from './types';

export interface LobbySnapshot {
  bankroll: number;
  bankrollHistory: BankrollPoint[];
  lastSessionDelta: number | null;
  reputation: ReputationData | null;
  tables: LobbyTable[];
  seatedTableId: string | null;
  hasActiveSession: boolean;
  seatedStakeLabel: string | null;
  seatedSince: string | null;
  pendingForgivenessCount: number;
  worldPace: WorldPace | null;
}

const VERSION = 1;
const keyFor = (userId: string) => `mpf:lobby-snapshot:v${VERSION}:${userId}`;

/** Read the last persisted lobby snapshot for a user, or null if none/unusable. */
export function readLobbyCache(userId: string | undefined): LobbySnapshot | null {
  if (!userId) return null;
  try {
    const raw = localStorage.getItem(keyFor(userId));
    return raw ? (JSON.parse(raw) as LobbySnapshot) : null;
  } catch {
    return null;
  }
}

/** Persist the inert display slice of a fresh lobby load. Best-effort. */
export function writeLobbyCache(userId: string | undefined, snapshot: LobbySnapshot): void {
  if (!userId) return;
  try {
    localStorage.setItem(keyFor(userId), JSON.stringify(snapshot));
  } catch {
    // Quota / private-mode / serialization failure — caching is best-effort.
  }
}
