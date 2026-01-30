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
  recommendation: string | null; // 'fold' | 'check' | 'call' | 'raise'
  position: string | null;
  phase: string | null;
  pot_total: number;
  cost_to_call: number;
  stack: number;
  opponent_stats: OpponentStat[];
}

export interface OpponentStat {
  name: string;
  vpip: number | null;
  pfr: number | null;
  aggression: number | null;
  style: string;
  hands_observed: number;
}

export interface CoachMessage {
  id: string;
  role: 'user' | 'coach';
  content: string;
  timestamp: number;
  type?: 'review';
}

export type CoachMode = 'proactive' | 'reactive' | 'off';
