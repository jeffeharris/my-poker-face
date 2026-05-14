export interface PlayerPsychology {
  narrative?: string;      // Third-person: "Feeling confident after that bluff"
  inner_voice?: string;    // First-person thought: "I've got this..."
  tilt_level: number;      // 0.0 - 1.0
  tilt_category: 'none' | 'mild' | 'moderate' | 'severe';
  tilt_source?: string;    // 'bad_beat', 'bluff_called', 'big_loss', etc.
  losing_streak: number;
}

export interface OpponentObservation {
  hands_observed: number;
  vpip: number;
  pfr: number;
  aggression_factor: number;
  play_style: string;
}

export interface PlayerPressureSummary {
  total_events: number;
  wins: number;
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
  headsup_wins: number;
  headsup_losses: number;
}

export interface LLMDebugInfo {
  provider: string;           // e.g., 'openai', 'anthropic', 'groq'
  model: string;              // e.g., 'gpt-5-nano', 'claude-sonnet-4'
  reasoning_effort?: string;  // e.g., 'low', 'medium', 'high'
  total_calls: number;        // Number of decisions made
  avg_latency_ms: number;     // Average response time
  avg_cost_per_call: number;  // Average cost per decision
}

export interface Player {
  name: string;
  nickname?: string;
  stack: number;
  bet: number;
  is_folded: boolean;
  is_all_in: boolean;
  is_human: boolean;
  hand?: { rank: string; suit: string }[];
  avatar_url?: string;
  avatar_emotion?: string;
  psychology?: PlayerPsychology;
  observation?: OpponentObservation;
  pressure_summary?: PlayerPressureSummary;
  is_rule_bot?: boolean;  // True for deterministic bots (CaseBot, GTO-Lite, BaselineSolver) — drives bot badge overlay
  last_action?: 'check' | 'call' | 'raise' | 'fold' | 'all_in' | null;  // Most recent action
  llm_debug?: LLMDebugInfo;  // AI model stats (debug mode only)
}