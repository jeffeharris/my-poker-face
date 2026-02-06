export interface PlayerStats {
  vpip: number;
  pfr: number;
  aggression: number;
  style: string;
  hands_observed: number;
}

export type ActionRecommendation = 'fold' | 'check' | 'call' | 'raise';

export interface CoachStats {
  equity: number | null;
  equity_vs_random: number | null;
  pot_odds: number | null;
  required_equity: number | null;
  is_positive_ev: boolean | null;
  ev_call: number | null;
  hand_strength: string | null;
  hand_rank: number | null;
  outs: number | null;
  outs_cards: string[] | null;
  recommendation: ActionRecommendation | null;
  raise_to: number | null; // Specific raise amount suggested by coach
  position: string | null;
  phase: string | null;
  pot_total: number;
  cost_to_call: number;
  stack: number;
  opponent_stats: OpponentStat[];
  player_stats: PlayerStats | null;
  available_actions: string[];
  position_context: string | null;
  progression?: CoachProgression;
}

export interface OpponentStat {
  name: string;
  stack: number;
  bet: number;
  is_all_in: boolean;
  vpip: number | null;
  pfr: number | null;
  aggression: number | null;
  style: string;
  hands_observed: number;
}

export interface FeedbackPromptData {
  hand: string;
  position: string;
  range_target: number;
  hand_number: number;
  context?: Record<string, unknown>;
}

export interface CoachMessage {
  id: string;
  role: 'user' | 'coach';
  content: string;
  timestamp: number;
  type?: 'review' | 'tip' | 'feedback_prompt';
  feedbackData?: FeedbackPromptData;
}

export type CoachMode = 'proactive' | 'reactive' | 'off';

// --- Progression types ---

export type SkillStateValue = 'introduced' | 'practicing' | 'reliable' | 'automatic';

export type CoachingModeValue = 'learn' | 'compete' | 'silent';

export interface SkillProgress {
  state: SkillStateValue;
  window_accuracy: number;
  total_opportunities: number;
  name: string;
  description: string;
  gate: number;
}

export interface FullSkillProgress extends SkillProgress {
  total_correct: number;
  streak_correct: number;
}

export interface CoachProgression {
  coaching_mode: CoachingModeValue;
  primary_skill: string | null;
  relevant_skills: string[];
  coaching_prompt: string;
  situation_tags: string[];
  skill_states: Record<string, SkillProgress>;
}

export interface GateProgressInfo {
  unlocked: boolean;
  unlocked_at: string | null;
  name: string;
  description: string;
}

export interface CoachProfile {
  self_reported_level: string;
  effective_level: string;
  onboarding_completed_at: string | null;
}

export interface ProgressionState {
  skill_states: Record<string, FullSkillProgress>;
  gate_progress: Record<string, GateProgressInfo>;
  profile: CoachProfile;
}
