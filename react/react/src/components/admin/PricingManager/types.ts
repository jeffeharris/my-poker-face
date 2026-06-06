// ============================================
// PricingManager — shared types
// ============================================

export interface PricingEntry {
  id: number;
  provider: string;
  model: string;
  unit: string;
  cost: number;
  valid_from: string | null;
  valid_until: string | null;
  notes: string | null;
}

export interface AlertState {
  type: 'success' | 'error' | 'info';
  message: string;
}

export interface PricingManagerProps {
  embedded?: boolean;
}

export interface NewPricing {
  provider: string;
  model: string;
  unit: string;
  cost: string;
  notes: string;
}

export interface PivotedModel {
  provider: string;
  model: string;
  costs: Record<string, number | null>;
  originalEntries: Record<string, PricingEntry>;
}

export type TabType = 'text' | 'image';
export type SortDirection = 'asc' | 'desc';

export interface PendingChange {
  provider: string;
  model: string;
  values: Record<string, string>;
  validFrom: string;
}

export interface SlideOutRef {
  isDirty: () => boolean;
  getValues: () => { values: Record<string, string>; validFrom: string };
}
