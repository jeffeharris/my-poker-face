/**
 * Adapters: map the shapes the rest of the app already passes
 * around (Player, lobby seat, personality blob) into the dossier's
 * input shape.
 *
 * Keep these dumb on purpose — no fetches, no transforms beyond
 * field renaming. Callers can override any field by spreading
 * the result.
 */

import type { Player } from '../../types/player';
import { config } from '../../config';
import type { CharacterDossierData } from './CharacterDetailCard';

/** Backend serves relative avatar paths ("/api/avatar/<name>/<emotion>/full").
 *  In dev the React app runs on a different port from Flask, so a bare
 *  relative path falls through to the Vite SPA fallback (index.html).
 *  Prefix with `config.API_URL` to point straight at the backend.
 *  In production `config.API_URL` is '' so this is a no-op. */
function absolutizeAvatarUrl(url: string | null | undefined): string | undefined {
  if (!url) return undefined;
  if (url.startsWith('http://') || url.startsWith('https://')) return url;
  return `${config.API_URL}${url}`;
}

/** Shape returned by the lobby seat endpoint for an AI slot. */
export interface LobbyAISeat {
  personality_id: string;
  name: string;
  nickname?: string;
  avatar_url?: string;
  emotion?: string;
  chips?: number;
  relationship_hint?: string;
  /** Optional fully-loaded personality block from `personality_repo`. */
  personality?: PersonalityBlock;
}

/** Subset of personalities.json that the dossier cares about.
 *  Trait/anchor values aren't here — the dossier route fetches
 *  them server-side; static callers only need the textual fields. */
export interface PersonalityBlock {
  name?: string;
  nickname?: string;
  play_style?: string;
  attitude?: string;
  confidence?: string;
  signature_line?: string;
}

/** Map a live `Player` (and optional personality blob) into dossier data. */
export function dossierFromPlayer(
  player: Player,
  personality?: PersonalityBlock,
  remark?: string
): CharacterDossierData {
  return {
    // Prefer the friendly name for the title; a tournament seat's `player.name`
    // is the raw field id, which would flash until the persona fetch resolves.
    // (No-op for cash, where `player.name` is already the display name.)
    name: personality?.name ?? player.nickname ?? player.name,
    nickname: personality?.nickname ?? player.nickname,
    avatarUrl: absolutizeAvatarUrl(player.avatar_url),
    emotion: player.avatar_emotion,
    playStyle: personality?.play_style,
    attitude: personality?.attitude,
    confidence: personality?.confidence,
    observed: player.observation && {
      handsObserved: player.observation.hands_observed,
      vpip: player.observation.vpip,
      pfr: player.observation.pfr,
      aggressionFactor: player.observation.aggression_factor,
    },
    chips: {
      atTable: player.stack,
    },
    remark: remark ?? personality?.signature_line,
  };
}

/** Map a lobby AI seat (and its loaded personality) into dossier data. */
export function dossierFromLobbySeat(seat: LobbyAISeat): CharacterDossierData {
  const p = seat.personality;
  return {
    name: seat.name,
    nickname: seat.nickname ?? p?.nickname,
    avatarUrl: absolutizeAvatarUrl(seat.avatar_url),
    emotion: seat.emotion,
    playStyle: p?.play_style,
    attitude: p?.attitude,
    confidence: p?.confidence,
    chips: seat.chips !== undefined ? { atTable: seat.chips } : undefined,
    affiliation: seat.relationship_hint
      ? {
          // Lobby's `relationship_hint` is the AI's POV of the player
          // ("rival", "trusted", "neutral"...). Surface it directly.
          relationship: inferRelationshipKind(seat.relationship_hint),
          relationshipNote: seat.relationship_hint,
        }
      : undefined,
    remark: p?.signature_line,
  };
}

function inferRelationshipKind(
  hint: string
): CharacterDossierData['affiliation'] extends infer A
  ? A extends { relationship?: infer R }
    ? R
    : never
  : never {
  const h = hint.toLowerCase();
  if (h.includes('rival') || h.includes('grudge')) return 'rival' as const;
  if (h.includes('trust') || h.includes('friend')) return 'friend' as const;
  if (h.includes('admir')) return 'admirer' as const;
  if (h.includes('antagonist') || h.includes('hostile')) return 'antagonist' as const;
  if (h.includes('sponsor') || h.includes('back')) return 'sponsor' as const;
  return 'neutral' as const;
}
