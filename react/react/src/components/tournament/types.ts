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
  finishing_position: number;
  eliminator: string | null;
}

export interface TournamentHuman {
  player_id: string;
  out: boolean;
  rank: number | null;
  stack: number | null;
  table_id: number | null;
}

export interface TournamentStandings {
  field_size: number;
  players_remaining: number;
  rounds: number;
  complete: boolean;
  winner: string | null;
  level: TournamentLevel;
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
export interface MttUpdateEvent {
  tournament_id: string;
  standings: TournamentStandings;
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
