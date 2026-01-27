import { type LucideIcon } from 'lucide-react';

export interface Theme {
  id: string;
  name: string;
  description: string;
  icon: LucideIcon;
  personalities?: Array<string | { name: string; game_mode?: string }>;
  themeDescription?: string;
  game_mode?: string;
  starting_stack?: number;
  big_blind?: number;
  blind_growth?: number;
  blinds_increase?: number;
  max_blind?: number;
}
