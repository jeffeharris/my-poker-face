import { useCallback, useEffect, useState } from 'react';
import { RefreshCw, AlertTriangle, Database, TrendingDown } from 'lucide-react';
import { adminFetch } from '../../../utils/api';
import { CensusChat } from './CensusChat';
import type { ChartCensusPayload } from './types';
import './ChartCensusPanel.css';

interface ChartCensusPanelProps {
  embedded?: boolean;
}

// Color a chart_source token consistently across the panel.
const SOURCE_CLASS: Record<string, string> = {
  chart_hit: 'src-hit',
  push_fold: 'src-pushfold',
  facing_all_in_veto: 'src-veto',
  chart_fallback: 'src-fallback',
};

function ago(epochSeconds?: number): string {
  if (!epochSeconds) return 'unknown time';
  const mins = Math.max(0, Math.round((Date.now() / 1000 - epochSeconds) / 60));
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 48) return `${hrs}h ago`;
  return `${Math.round(hrs / 24)}d ago`;
}

function Bar({ pct, cls }: { pct: number; cls?: string }) {
  return (
    <div className="cc-bar-track">
      <div className={`cc-bar-fill ${cls ?? ''}`} style={{ width: `${Math.min(100, pct)}%` }} />
    </div>
  );
}

export function ChartCensusPanel({ embedded }: ChartCensusPanelProps) {
  const [data, setData] = useState<ChartCensusPayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [missing, setMissing] = useState<string | null>(null);

  const fetchData = useCallback(async () => {
    setLoading(true);
    try {
      const resp = await adminFetch('/api/admin/chart-census');
      if (resp.status === 404) {
        const body = await resp.json().catch(() => ({}));
        setMissing(body.message || 'No census artifact has been generated yet.');
        setData(null);
        setError(null);
        return;
      }
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const payload: ChartCensusPayload = await resp.json();
      setData(payload);
      setMissing(null);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void fetchData();
  }, [fetchData]);

  return (
    <div className={`chart-census-panel ${embedded ? 'embedded' : ''}`}>
      <div className="cc-header">
        <h2>Chart Opportunity Census</h2>
        <span className="cc-sub">
          Where preflop decisions land · money at risk · by archetype · fall-throughs
        </span>
        <button className="cc-refresh" onClick={() => void fetchData()} title="Reload artifact">
          <RefreshCw size={16} />
        </button>
      </div>

      {loading && <div className="cc-status">Loading census…</div>}

      {error && (
        <div className="cc-banner error">
          <AlertTriangle size={16} /> Failed to load census: {error}
        </div>
      )}

      {missing && !loading && (
        <div className="cc-empty">
          <Database size={28} />
          <p>{missing}</p>
          <p className="cc-empty-hint">Generate it (sims run in Docker):</p>
          <pre>
            {`docker compose exec backend python3 scripts/chart_census_sim.py \\
    --db /tmp/census.db --hands 800 --jobs 6

docker compose exec backend python3 scripts/chart_census.py \\
    /tmp/census.db --json data/chart_census.json --quiet`}
          </pre>
          <button className="cc-btn" onClick={() => void fetchData()}>
            <RefreshCw size={14} /> Check again
          </button>
        </div>
      )}

      {data && !loading && <Census data={data} />}
    </div>
  );
}

function Census({ data }: { data: ChartCensusPayload }) {
  const { meta, spot_census: spot, money_census: money, archetype_matrix: am } = data;
  const audit = data.fallthrough_audit;

  return (
    <>
      {/* KPI row */}
      <div className="cc-kpis">
        <Kpi label="Preflop decisions" value={meta.total_preflop_decisions.toLocaleString()} />
        <Kpi
          label="Fall-through rate"
          value={`${audit.pct.toFixed(1)}%`}
          sub={`${audit.total_fallthrough.toLocaleString()} decisions`}
          warn={audit.pct >= 5}
        />
        <Kpi label="Total bb at risk" value={Math.round(money.total_risk_bb).toLocaleString()} />
        <Kpi label="Opportunity classes" value={String(audit.classes.length)} />
        <Kpi
          label="Generated"
          value={ago(data._generated_at)}
          sub={`${meta.fields.length} fields`}
        />
      </div>

      <CensusChat />

      {/* 1. Fall-through audit FIRST — it's the actionable output */}
      <Section
        title="Fall-through audit — wanted chart behavior, got fallback"
        icon={<TrendingDown size={18} />}
      >
        {audit.classes.length === 0 ? (
          <p className="cc-muted">Every decision was served by a specialized chart.</p>
        ) : (
          <table className="cc-table">
            <thead>
              <tr>
                <th>Opportunity class</th>
                <th className="num">Count</th>
                <th className="num">% all</th>
                <th className="num">bb at risk</th>
                <th>By field</th>
              </tr>
            </thead>
            <tbody>
              {audit.classes.map((c) => (
                <tr key={c.klass}>
                  <td className="mono">{c.klass}</td>
                  <td className="num">{c.count.toLocaleString()}</td>
                  <td className="num">{c.pct_all.toFixed(1)}%</td>
                  <td className="num strong">{Math.round(c.risk_bb).toLocaleString()}</td>
                  <td className="chips">
                    {Object.entries(c.by_field)
                      .slice(0, 6)
                      .map(([f, n]) => (
                        <span key={f} className="cc-chip">
                          {f} <b>{n}</b>
                        </span>
                      ))}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Section>

      {/* 2. Spot census */}
      <Section title="Spot census — where decisions land">
        <div className="cc-cols">
          <BarList
            title="By scenario"
            rows={spot.by_scenario.map((r) => ({
              key: r.scenario,
              label: r.scenario,
              count: r.count,
              pct: r.pct,
            }))}
          />
          <BarList
            title="Chart source (what produced the action)"
            rows={spot.by_chart_source.map((r) => ({
              key: r.source,
              label: r.source,
              count: r.count,
              pct: r.pct,
              cls: SOURCE_CLASS[r.source],
            }))}
          />
          <BarList
            title="Base chart selected"
            rows={spot.by_chart_label.map((r) => ({
              key: r.label,
              label: r.label,
              count: r.count,
              pct: r.pct,
            }))}
          />
        </div>
      </Section>

      {/* 3. Money census */}
      <Section title="Money census — bb at risk by spot">
        <p className="cc-muted">
          risk_bb = bb the decision commits (all-in = eff. stack, raise = raise-to, call =
          cost-to-call, fold/check = 0). Surfaces rare-but-huge spots that decision counts hide.
        </p>
        <div className="cc-cols">
          <table className="cc-table">
            <thead>
              <tr>
                <th>Scenario</th>
                <th className="num">n</th>
                <th className="num">Σ bb</th>
                <th className="num">% risk</th>
                <th className="num">mean</th>
                <th className="num">max</th>
              </tr>
            </thead>
            <tbody>
              {money.by_scenario.map((r) => (
                <tr key={r.scenario}>
                  <td className="mono">{r.scenario}</td>
                  <td className="num">{r.n.toLocaleString()}</td>
                  <td className="num strong">{Math.round(r.sum_bb).toLocaleString()}</td>
                  <td className="num">{r.pct_risk.toFixed(1)}%</td>
                  <td className="num">{r.mean_bb.toFixed(1)}</td>
                  <td className="num">{r.max_bb.toFixed(0)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <table className="cc-table">
            <thead>
              <tr>
                <th colSpan={2}>Top spots by bb at risk</th>
                <th className="num">Σ bb</th>
                <th className="num">% risk</th>
              </tr>
            </thead>
            <tbody>
              {money.top_spots.slice(0, 10).map((r) => (
                <tr key={`${r.scenario}/${r.source}`}>
                  <td className="mono">{r.scenario}</td>
                  <td>
                    <span className={`cc-dot ${SOURCE_CLASS[r.source] ?? ''}`} />
                    {r.source}
                  </td>
                  <td className="num strong">{Math.round(r.sum_bb).toLocaleString()}</td>
                  <td className="num">{r.pct_risk.toFixed(1)}%</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Section>

      {/* 4. Archetype matrix */}
      <Section title="Archetype matrix — spot share & fall-through by opponent field">
        <div className="cc-matrix-wrap">
          <table className="cc-table cc-matrix">
            <thead>
              <tr>
                <th>scenario</th>
                {am.fields.map((f) => (
                  <th key={f} className="num">
                    {f}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {am.scenarios.map((scen) => (
                <tr key={scen}>
                  <td className="mono">{scen}</td>
                  {am.fields.map((f) => {
                    const v = am.spot_share_pct[f]?.[scen] ?? 0;
                    return (
                      <td key={f} className="num heat" style={{ background: heat(v) }}>
                        {v >= 0.05 ? `${v.toFixed(1)}%` : '·'}
                      </td>
                    );
                  })}
                </tr>
              ))}
              <tr className="cc-row-sep">
                <td className="mono dim">decisions</td>
                {am.fields.map((f) => (
                  <td key={f} className="num dim">
                    {am.field_decisions[f]?.toLocaleString()}
                  </td>
                ))}
              </tr>
              <tr>
                <td className="mono dim">bb at risk</td>
                {am.fields.map((f) => (
                  <td key={f} className="num dim">
                    {Math.round(am.field_risk_bb[f] ?? 0).toLocaleString()}
                  </td>
                ))}
              </tr>
              <tr>
                <td className="mono strong">fall-through</td>
                {am.fields.map((f) => {
                  const ft = am.field_fallthrough[f];
                  const pct = ft?.pct ?? 0;
                  return (
                    <td key={f} className="num heat" style={{ background: heatRed(pct) }}>
                      {pct.toFixed(1)}%
                    </td>
                  );
                })}
              </tr>
            </tbody>
          </table>
        </div>
        <p className="cc-muted">
          Same charts, very different coverage by field — the fall-through row makes the
          archetype-dependent gaps obvious.
        </p>
      </Section>
    </>
  );
}

function Section({
  title,
  icon,
  children,
}: {
  title: string;
  icon?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <section className="cc-section">
      <h3>
        {icon}
        {title}
      </h3>
      {children}
    </section>
  );
}

function Kpi({
  label,
  value,
  sub,
  warn,
}: {
  label: string;
  value: string;
  sub?: string;
  warn?: boolean;
}) {
  return (
    <div className={`cc-kpi ${warn ? 'warn' : ''}`}>
      <div className="cc-kpi-value">{value}</div>
      <div className="cc-kpi-label">{label}</div>
      {sub && <div className="cc-kpi-sub">{sub}</div>}
    </div>
  );
}

interface BarRow {
  key: string;
  label: string;
  count: number;
  pct: number;
  cls?: string;
}
function BarList({ title, rows }: { title: string; rows: BarRow[] }) {
  return (
    <div className="cc-barlist">
      <div className="cc-barlist-title">{title}</div>
      {rows.map((r) => (
        <div key={r.key} className="cc-barrow">
          <span className="cc-barrow-label mono">{r.label}</span>
          <Bar pct={r.pct} cls={r.cls} />
          <span className="cc-barrow-pct">{r.pct.toFixed(1)}%</span>
          <span className="cc-barrow-count">{r.count.toLocaleString()}</span>
        </div>
      ))}
    </div>
  );
}

// Green heat for spot-share %, red heat for fall-through %.
function heat(pct: number): string {
  if (pct < 0.05) return 'transparent';
  const a = Math.min(0.5, pct / 100 + 0.04);
  return `rgba(86, 211, 100, ${a})`;
}
function heatRed(pct: number): string {
  if (pct < 0.05) return 'transparent';
  const a = Math.min(0.55, pct / 40);
  return `rgba(248, 81, 73, ${a})`;
}
