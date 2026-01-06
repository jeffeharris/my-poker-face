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
}