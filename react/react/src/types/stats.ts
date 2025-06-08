export interface PlayerSummary {
  total_events: number;
  big_wins: number;
  big_losses: number;
  successful_bluffs: number;
  bluffs_caught: number;
  bad_beats: number;
  eliminations: number;
  biggest_pot_won: number;
  biggest_pot_lost: number;
  tilt_score: number;
  aggression_score: number;
  signature_move: string;
}

export interface LeaderboardEntry {
  name: string;
  [key: string]: any;
}

export interface SessionSummary {
  session_duration: number;
  total_events: number;
  biggest_pot: number;
  player_summaries: { [name: string]: PlayerSummary };
  leaderboards: {
    biggest_winners: LeaderboardEntry[];
    master_bluffers: LeaderboardEntry[];
    most_aggressive: LeaderboardEntry[];
    bad_beat_victims: LeaderboardEntry[];
    tilt_masters: LeaderboardEntry[];
  };
  fun_facts: string[];
}