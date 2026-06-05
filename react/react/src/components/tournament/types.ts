/**
 * Types mirroring the backend `TournamentSession.standings_view()` payload
 * (tournament/session.py) and the /api/tournament/* routes.
 */

export interface TournamentLevel {
  level: number;
  small_blind: number;
  big_blind: number;
  ante: number;
}

export interface TournamentSeat {
  seat: number;
  player_id: string | null;
  /** Friendly display name resolved at the Flask boundary (tournament_naming.named_standings);
   *  falls back to player_id when unresolved. */
  name?: string | null;
  stack: number | null;
  archetype: string | null;
  is_human: boolean;
  is_button: boolean;
}

export interface TournamentTable {
  table_id: number;
  size: number;
  is_human_table: boolean;
  seats: TournamentSeat[];
}

export interface TournamentElimination {
  player_id: string;
  /** Resolved display name for player_id; falls back to player_id when unresolved. */
  name?: string | null;
  finishing_position: number;
  eliminator: string | null;
  /** Resolved display name for eliminator; falls back to eliminator when unresolved. */
  eliminator_name?: string | null;
}

export interface TournamentHuman {
  player_id: string;
  out: boolean;
  rank: number | null;
  stack: number | null;
  table_id: number | null;
  in_money: boolean;
}

/** Blinds after the current level + how many of the human's hands until it hits
 *  (tournament time is player-gated, so the clock is in hands, not minutes). */
export interface TournamentNextLevel {
  level: number;
  small_blind: number;
  big_blind: number;
  ante: number;
  hands_until: number;
}

export interface TournamentLeader {
  rank: number;
  player_id: string;
  /** Resolved display name; falls back to player_id when unresolved. */
  name?: string | null;
  stack: number;
  is_human: boolean;
}

/** In-the-money status — display-only until real payouts ship. */
export interface TournamentPayout {
  paid_places: number;
  players_to_money: number;
  on_bubble: boolean;
  in_money: boolean;
}

export interface TournamentStandings {
  field_size: number;
  players_remaining: number;
  rounds: number;
  complete: boolean;
  winner: string | null;
  /** Resolved display name for the winner; falls back to winner (raw id) when unresolved. */
  winner_name?: string | null;
  level: TournamentLevel;
  next_level: TournamentNextLevel | null;
  leaders: TournamentLeader[];
  payout: TournamentPayout;
  human: TournamentHuman;
  tables: TournamentTable[];
  recent_eliminations: TournamentElimination[];
}

export interface TournamentLobbyActive {
  tournament_id: string;
  created_at: string;
  standings: TournamentStandings;
}

export interface TournamentLobbyResponse {
  has_active: boolean;
  active: TournamentLobbyActive | null;
  defaults: { field_size: number; table_size: number; starting_stack: number };
}

export interface RegisterRequest {
  field_size?: number;
  table_size?: number;
  starting_stack?: number;
  seed?: number;
  resolver?: 'fake' | 'engine';
}

export interface RegisterResponse {
  tournament_id: string;
  standings: TournamentStandings;
}

/**
 * Multi-table tournament (MTT) realtime events, pushed by the backend to the
 * owner's lobby room (`mtt_*` namespace — deliberately distinct from the legacy
 * single-table `tournament_complete`, which has a different payload and feeds
 * the `TournamentResult` end screen). The game-page socket is joined to the
 * lobby room on connect, so these arrive while the human is at the live table.
 * See flask_app/handlers/tournament_game_builder.py `_emit_tournament`.
 */
/**
 * Activity beats — narratable things that happened across the field since the
 * human's last hand (time is player-gated, so they arrive in a burst at each
 * hand boundary). Built by tournament/beats.py and carried on `mtt_update`.
 * Rendered on the felt ticker, as rare structural toasts, and in the hub feed.
 * Each beat carries the producing `round` so the client can build a stable key.
 */
export interface TournamentKnockoutBeat {
  type: 'knockout';
  round: number;
  player_id: string;
  finishing_position: number;
  eliminator: string | null;
  is_human: boolean;
}
export interface TournamentTableBreakBeat {
  type: 'table_break';
  round: number;
  table_id: number;
}
export interface TournamentBubbleBeat {
  type: 'bubble';
  round: number;
  player_id: string;
  paid_places: number;
}
export interface TournamentMilestoneBeat {
  type: 'milestone';
  round: number;
  kind: 'final_table' | 'down_to' | 'heads_up';
  remaining: number;
}
export interface TournamentLevelUpBeat {
  type: 'level_up';
  round: number;
  level: number;
  small_blind: number;
  big_blind: number;
  ante: number;
}
/** One-hand heads-up: blinds raise to this level on the *next* hand. */
export interface TournamentLevelUpNextBeat {
  type: 'level_up_next';
  round: number;
  level: number;
  small_blind: number;
  big_blind: number;
  ante: number;
}
export type TournamentBeat =
  | TournamentKnockoutBeat
  | TournamentTableBreakBeat
  | TournamentBubbleBeat
  | TournamentMilestoneBeat
  | TournamentLevelUpBeat
  | TournamentLevelUpNextBeat;

export interface MttUpdateEvent {
  tournament_id: string;
  standings: TournamentStandings;
  beats?: TournamentBeat[];
}

export interface MttRelocatedEvent {
  tournament_id: string;
  table_id: number;
}

export interface MttEliminatedEvent {
  tournament_id: string;
  finishing_position: number | null;
}

export interface MttCompleteEvent {
  tournament_id: string;
  standings: TournamentStandings;
}
