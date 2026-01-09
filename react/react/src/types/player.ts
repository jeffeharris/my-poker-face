export interface PlayerPsychology {
  narrative?: string;      // Third-person: "Feeling confident after that bluff"
  inner_voice?: string;    // First-person thought: "I've got this..."
  tilt_level: number;      // 0.0 - 1.0
  tilt_category: 'none' | 'mild' | 'moderate' | 'severe';
  tilt_source?: string;    // 'bad_beat', 'bluff_called', 'big_loss', etc.
  losing_streak: number;
}

export interface Player {
  name: string;
  stack: number;
  bet: number;
  is_folded: boolean;
  is_all_in: boolean;
  is_human: boolean;
  hand?: { rank: string; suit: string }[];
  avatar_url?: string;
  avatar_emotion?: string;
  psychology?: PlayerPsychology;
}