// Mirrors the JSON payload from scripts/chart_census.py (build_payload) served
// by GET /api/admin/chart-census.

export interface CensusMeta {
  source_db: string;
  total_preflop_decisions: number;
  push_fold_bb: number;
  fields: string[];
  depth_tags: string[];
}

export interface ScenarioCount {
  scenario: string;
  count: number;
  pct: number;
}
export interface LabelCount {
  label: string;
  count: number;
  pct: number;
}
export interface SourceCount {
  source: string;
  count: number;
  pct: number;
}
export interface ScenarioSourceCount {
  scenario: string;
  source: string;
  count: number;
  pct: number;
}

export interface SpotCensus {
  total: number;
  by_scenario: ScenarioCount[];
  by_chart_label: LabelCount[];
  by_chart_source: SourceCount[];
  scenario_x_source: ScenarioSourceCount[];
}

export interface MoneyScenarioRow {
  scenario: string;
  n: number;
  sum_bb: number;
  pct_risk: number;
  mean_bb: number;
  max_bb: number;
}
export interface MoneyTopSpot {
  scenario: string;
  source: string;
  n: number;
  sum_bb: number;
  pct_risk: number;
}
export interface MoneyCensus {
  total_risk_bb: number;
  by_scenario: MoneyScenarioRow[];
  top_spots: MoneyTopSpot[];
}

export interface FieldFallthrough {
  count: number;
  total: number;
  pct: number;
}
export interface ArchetypeMatrix {
  fields: string[];
  scenarios: string[];
  spot_share_pct: Record<string, Record<string, number>>;
  field_decisions: Record<string, number>;
  field_risk_bb: Record<string, number>;
  field_fallthrough: Record<string, FieldFallthrough>;
}

export interface FallthroughClass {
  klass: string;
  count: number;
  pct_all: number;
  risk_bb: number;
  by_field: Record<string, number>;
}
export interface FallthroughAudit {
  total_decisions: number;
  total_fallthrough: number;
  pct: number;
  classes: FallthroughClass[];
}

export interface ChartCensusPayload {
  meta: CensusMeta;
  _generated_at?: number;
  spot_census: SpotCensus;
  money_census: MoneyCensus;
  archetype_matrix: ArchetypeMatrix;
  fallthrough_audit: FallthroughAudit;
}
