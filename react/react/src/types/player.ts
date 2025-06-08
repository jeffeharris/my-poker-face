export interface Player {
  name: string;
  stack: number;
  bet: number;
  is_folded: boolean;
  is_all_in: boolean;
  is_human: boolean;
  hand?: string[];
}