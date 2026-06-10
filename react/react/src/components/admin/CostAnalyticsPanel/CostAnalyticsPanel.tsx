import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  ResponsiveContainer,
  AreaChart,
  Area,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Cell,
  PieChart,
  Pie,
  Legend,
} from 'recharts';
import { ArrowLeft, RefreshCw, AlertTriangle } from 'lucide-react';
import { adminFetch } from '../../../utils/api';
import { useVisiblePolling } from '../../../hooks/useVisiblePolling';
import type {
  CostOverview,
  OwnerDetail,
  CostRange,
  CallTypeCost,
  ModelCost,
  GameCost,
  TimeseriesPoint,
} from './types';
import {
  REFRESH_MS,
  RANGE_OPTIONS,
  CHART_COLORS,
  callTypeLabel,
  fmtCost,
  fmtCount,
  avgCostPerCall,
  shortOwner,
  shortGame,
} from './costUtils';
import { CallDetailDrawer } from './CallDetailDrawer';
import './CostAnalyticsPanel.css';

interface CostAnalyticsPanelProps {
  embedded?: boolean;
}

interface DrawerState {
  ownerId?: string;
  callType?: string;
  gameId?: string;
  title: string;
}

export function CostAnalyticsPanel({ embedded }: CostAnalyticsPanelProps) {
  const [range, setRange] = useState<CostRange>('7d');
  const [overview, setOverview] = useState<CostOverview | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Drill: selected owner + its detail payload.
  const [selectedOwner, setSelectedOwner] = useState<string | null>(null);
  const [ownerDetail, setOwnerDetail] = useState<OwnerDetail | null>(null);
  const [ownerLoading, setOwnerLoading] = useState(false);

  // Deepest drill: raw-call drawer.
  const [drawer, setDrawer] = useState<DrawerState | null>(null);

  const fetchOverview = useCallback(async () => {
    try {
      const resp = await adminFetch(`/api/admin/cost-analytics/overview?range=${range}`);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data: CostOverview = await resp.json();
      setOverview(data);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [range]);

  const fetchOwnerDetail = useCallback(
    async (ownerId: string) => {
      setOwnerLoading(true);
      try {
        const resp = await adminFetch(
          `/api/admin/cost-analytics/owner/${encodeURIComponent(ownerId)}?range=${range}`
        );
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const data: OwnerDetail = await resp.json();
        setOwnerDetail(data);
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        setOwnerLoading(false);
      }
    },
    [range]
  );

  useVisiblePolling(fetchOverview, REFRESH_MS);

  // Just set the selection; the effect below fetches the detail (and refetches
  // on range change), so there's a single fetch path.
  const openOwner = useCallback((ownerId: string) => {
    setSelectedOwner(ownerId);
    setOwnerDetail(null);
  }, []);

  const closeOwner = useCallback(() => {
    setSelectedOwner(null);
    setOwnerDetail(null);
  }, []);

  const handleRangeChange = useCallback((r: CostRange) => {
    setRange(r);
  }, []);

  // When the range changes while drilled into an owner, refetch its detail
  // under the new window. (The overview itself refetches via useVisiblePolling,
  // whose callback identity changes with `range`.)
  useEffect(() => {
    if (selectedOwner) {
      setOwnerDetail(null);
      void fetchOwnerDetail(selectedOwner);
    }
  }, [range, selectedOwner, fetchOwnerDetail]);

  return (
    <div className={`cost-analytics-panel${embedded ? ' embedded' : ''}`}>
      <div className="cost-header">
        <h2>Cost Analytics</h2>
        <span className="cost-sub">LLM + image-gen spend · estimated USD</span>
        <div className="cost-range-group" role="group" aria-label="Time range">
          {RANGE_OPTIONS.map((opt) => (
            <button
              key={opt.value}
              className={`cost-range-btn${range === opt.value ? ' active' : ''}`}
              onClick={() => handleRangeChange(opt.value)}
            >
              {opt.label}
            </button>
          ))}
        </div>
        <button
          className="cost-refresh"
          onClick={() => {
            void fetchOverview();
            if (selectedOwner) void fetchOwnerDetail(selectedOwner);
          }}
          title="Refresh now"
        >
          <RefreshCw size={16} />
        </button>
      </div>

      {error && <div className="cost-banner cost-error">Error: {error}</div>}
      {loading && !overview && <div className="cost-status">Loading…</div>}

      {overview && !selectedOwner && (
        <OverviewView
          overview={overview}
          onSelectOwner={openOwner}
          onSelectCallType={(ct) =>
            setDrawer({ callType: ct, title: `${callTypeLabel(ct)} — all owners` })
          }
          onSelectGame={(gid) => setDrawer({ gameId: gid, title: `Game ${gid}` })}
        />
      )}

      {selectedOwner && (
        <div className="cost-owner-detail">
          <button className="cost-back" onClick={closeOwner}>
            <ArrowLeft size={16} /> All owners
          </button>
          <div className="cost-owner-title">
            <h3>{selectedOwner}</h3>
            {ownerDetail && (
              <span className="cost-sub">
                {fmtCost(ownerDetail.total_cost)} · {fmtCount(ownerDetail.total_calls)} calls
              </span>
            )}
            <button
              className="cost-link"
              onClick={() =>
                setDrawer({ ownerId: selectedOwner, title: `${selectedOwner} — raw calls` })
              }
            >
              View raw calls →
            </button>
          </div>
          {ownerLoading && !ownerDetail && <div className="cost-status">Loading…</div>}
          {ownerDetail && (
            <BreakdownView
              timeseries={ownerDetail.timeseries}
              byCallType={ownerDetail.by_call_type}
              byModel={ownerDetail.by_model}
              byGame={ownerDetail.by_game}
              onSelectCallType={(ct) =>
                setDrawer({
                  ownerId: selectedOwner,
                  callType: ct,
                  title: `${selectedOwner} · ${callTypeLabel(ct)}`,
                })
              }
              onSelectGame={(gid) => setDrawer({ gameId: gid, title: `Game ${gid}` })}
            />
          )}
        </div>
      )}

      {drawer && (
        <CallDetailDrawer
          range={range}
          ownerId={drawer.ownerId}
          callType={drawer.callType}
          gameId={drawer.gameId}
          title={drawer.title}
          onClose={() => setDrawer(null)}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Overview: KPIs + owner bar chart + shared breakdown
// ---------------------------------------------------------------------------

function OverviewView({
  overview,
  onSelectOwner,
  onSelectCallType,
  onSelectGame,
}: {
  overview: CostOverview;
  onSelectOwner: (ownerId: string) => void;
  onSelectCallType: (callType: string) => void;
  onSelectGame: (gameId: string) => void;
}) {
  const { summary, by_owner, by_call_type, by_model, by_game, uncosted, timeseries } = overview;

  const ownerData = useMemo(
    () =>
      by_owner.slice(0, 12).map((o) => ({
        owner_id: o.owner_id,
        label: shortOwner(o.owner_id),
        total_cost: Number(o.total_cost.toFixed(4)),
      })),
    [by_owner]
  );

  return (
    <>
      {uncosted && uncosted.total > 0 && (
        <div className="cost-banner cost-warn">
          <AlertTriangle size={18} />
          <div>
            <strong>{fmtCount(uncosted.total)} successful calls have no cost</strong> — these models
            are missing a pricing SKU and silently count as $0:{' '}
            {uncosted.by_model
              .slice(0, 6)
              .map((m) => `${m.provider}/${m.model} (${fmtCount(m.calls)})`)
              .join(', ')}
            {uncosted.by_model.length > 6 ? ' …' : ''}
          </div>
        </div>
      )}

      <div className="cost-kpi-row">
        <KpiCard label="Total cost" value={fmtCost(summary.total_cost)} accent />
        <KpiCard label="Total calls" value={fmtCount(summary.total_calls)} />
        <KpiCard
          label="Avg cost / call"
          value={fmtCost(avgCostPerCall(summary.total_cost, summary.total_calls))}
        />
        <KpiCard
          label="Error rate"
          value={`${summary.error_rate.toFixed(1)}%`}
          warn={summary.error_rate > 5}
        />
        <KpiCard label="Avg latency" value={`${Math.round(summary.avg_latency)}ms`} />
      </div>

      <CostOverTime timeseries={timeseries} />

      <div className="cost-card">
        <div className="cost-card-head">
          <h4>Cost by owner</h4>
          <span className="cost-sub">Top {ownerData.length} · click a bar to drill in</span>
        </div>
        <ResponsiveContainer width="100%" height={Math.max(220, ownerData.length * 30)}>
          <BarChart data={ownerData} layout="vertical" margin={{ left: 8, right: 16 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#2a2a2a" horizontal={false} />
            <XAxis type="number" tick={{ fill: '#999', fontSize: 11 }} tickFormatter={fmtCost} />
            <YAxis
              type="category"
              dataKey="label"
              width={150}
              tick={{ fill: '#ccc', fontSize: 11 }}
            />
            <Tooltip
              cursor={{ fill: 'rgba(255,255,255,0.04)' }}
              contentStyle={tooltipStyle}
              formatter={(value) => [fmtCost(Number(value)), 'Cost'] as [string, string]}
            />
            <Bar
              dataKey="total_cost"
              radius={[0, 3, 3, 0]}
              cursor="pointer"
              onClick={(d) => {
                const ownerId = (d as unknown as { owner_id?: string })?.owner_id;
                if (ownerId) onSelectOwner(ownerId);
              }}
            >
              {ownerData.map((_, i) => (
                <Cell key={i} fill={CHART_COLORS[i % CHART_COLORS.length]} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>

      <BreakdownView
        timeseries={null}
        byCallType={by_call_type}
        byModel={by_model}
        byGame={by_game}
        showGameOwner
        onSelectCallType={onSelectCallType}
        onSelectGame={onSelectGame}
      />
    </>
  );
}

// ---------------------------------------------------------------------------
// Shared: call-type pie + bar, model table, optional timeseries
// ---------------------------------------------------------------------------

function BreakdownView({
  timeseries,
  byCallType,
  byModel,
  byGame,
  showGameOwner,
  onSelectCallType,
  onSelectGame,
}: {
  timeseries: TimeseriesPoint[] | null;
  byCallType: CallTypeCost[];
  byModel: ModelCost[];
  byGame: GameCost[];
  /** Show the owner column in the game table (overview = all owners). */
  showGameOwner?: boolean;
  onSelectCallType: (callType: string) => void;
  onSelectGame: (gameId: string) => void;
}) {
  const pieData = useMemo(
    () =>
      byCallType
        .filter((c) => c.total_cost > 0)
        .map((c) => ({
          name: callTypeLabel(c.call_type),
          call_type: c.call_type,
          value: Number(c.total_cost.toFixed(4)),
        })),
    [byCallType]
  );

  return (
    <>
      {timeseries && <CostOverTime timeseries={timeseries} />}

      <div className="cost-grid-2">
        <div className="cost-card">
          <div className="cost-card-head">
            <h4>Cost by call type</h4>
          </div>
          {pieData.length === 0 ? (
            <div className="cost-status">No billable cost in range.</div>
          ) : (
            <ResponsiveContainer width="100%" height={280}>
              <PieChart>
                <Pie
                  data={pieData}
                  dataKey="value"
                  nameKey="name"
                  cx="50%"
                  cy="50%"
                  outerRadius={90}
                  innerRadius={45}
                  paddingAngle={1}
                  onClick={(d) => {
                    const ct = (d as unknown as { call_type?: string })?.call_type;
                    if (ct) onSelectCallType(ct);
                  }}
                  cursor="pointer"
                >
                  {pieData.map((_, i) => (
                    <Cell key={i} fill={CHART_COLORS[i % CHART_COLORS.length]} />
                  ))}
                </Pie>
                <Tooltip
                  contentStyle={tooltipStyle}
                  formatter={(value) => fmtCost(Number(value))}
                />
                <Legend wrapperStyle={{ fontSize: 11, color: '#ccc' }} />
              </PieChart>
            </ResponsiveContainer>
          )}
        </div>

        <div className="cost-card">
          <div className="cost-card-head">
            <h4>Call-type detail</h4>
            <span className="cost-sub">click a row for raw calls</span>
          </div>
          <div className="cost-table-wrap cost-table-scroll">
            <table className="cost-table">
              <thead>
                <tr>
                  <th>Call type</th>
                  <th className="num">Calls</th>
                  <th className="num">Cost</th>
                  <th className="num">Avg/call</th>
                </tr>
              </thead>
              <tbody>
                {byCallType.map((c) => (
                  <tr
                    key={c.call_type}
                    className="cost-row-click"
                    onClick={() => onSelectCallType(c.call_type)}
                  >
                    <td>{callTypeLabel(c.call_type)}</td>
                    <td className="num">{fmtCount(c.total_calls)}</td>
                    <td className="num">{fmtCost(c.total_cost)}</td>
                    <td className="num">{fmtCost(avgCostPerCall(c.total_cost, c.total_calls))}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>

      <div className="cost-card">
        <div className="cost-card-head">
          <h4>Cost by model</h4>
        </div>
        <div className="cost-table-wrap">
          <table className="cost-table">
            <thead>
              <tr>
                <th>Provider</th>
                <th>Model</th>
                <th className="num">Calls</th>
                <th className="num">Input tok</th>
                <th className="num">Output tok</th>
                <th className="num">Cost</th>
              </tr>
            </thead>
            <tbody>
              {byModel.map((m) => (
                <tr key={`${m.provider}/${m.model}`}>
                  <td>{m.provider}</td>
                  <td>{m.model}</td>
                  <td className="num">{fmtCount(m.total_calls)}</td>
                  <td className="num">{fmtCount(m.input_tokens)}</td>
                  <td className="num">{fmtCount(m.output_tokens)}</td>
                  <td className="num">{fmtCost(m.total_cost)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div className="cost-card">
        <div className="cost-card-head">
          <h4>Cost by game</h4>
          <span className="cost-sub">top {byGame.length} · click a row for raw calls</span>
        </div>
        {byGame.length === 0 ? (
          <div className="cost-status">No game-attributed calls in range.</div>
        ) : (
          <div className="cost-table-wrap cost-table-scroll">
            <table className="cost-table">
              <thead>
                <tr>
                  <th>Game</th>
                  {showGameOwner && <th>Owner</th>}
                  <th className="num">Hands</th>
                  <th className="num">Calls</th>
                  <th className="num">Cost</th>
                  <th className="num">Avg/call</th>
                </tr>
              </thead>
              <tbody>
                {byGame.map((g) => (
                  <tr
                    key={g.game_id}
                    className="cost-row-click"
                    onClick={() => onSelectGame(g.game_id)}
                  >
                    <td className="cost-ellipsis" title={g.game_id}>
                      {shortGame(g.game_id)}
                    </td>
                    {showGameOwner && (
                      <td className="cost-ellipsis" title={g.owner_id}>
                        {g.owner_id}
                      </td>
                    )}
                    <td className="num">{g.max_hand != null ? g.max_hand : '—'}</td>
                    <td className="num">{fmtCount(g.total_calls)}</td>
                    <td className="num">{fmtCost(g.total_cost)}</td>
                    <td className="num">{fmtCost(avgCostPerCall(g.total_cost, g.total_calls))}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </>
  );
}

// ---------------------------------------------------------------------------
// Pieces
// ---------------------------------------------------------------------------

function CostOverTime({ timeseries }: { timeseries: TimeseriesPoint[] }) {
  const data = useMemo(
    () =>
      timeseries.map((p) => ({
        period: p.period,
        total_cost: Number(p.total_cost.toFixed(4)),
        total_calls: p.total_calls,
      })),
    [timeseries]
  );

  return (
    <div className="cost-card">
      <div className="cost-card-head">
        <h4>Cost over time</h4>
      </div>
      <ResponsiveContainer width="100%" height={220}>
        <AreaChart data={data} margin={{ left: 4, right: 16, top: 8 }}>
          <defs>
            <linearGradient id="costGrad" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#56d364" stopOpacity={0.5} />
              <stop offset="100%" stopColor="#56d364" stopOpacity={0.02} />
            </linearGradient>
          </defs>
          <CartesianGrid strokeDasharray="3 3" stroke="#2a2a2a" />
          <XAxis dataKey="period" tick={{ fill: '#999', fontSize: 10 }} minTickGap={24} />
          <YAxis tick={{ fill: '#999', fontSize: 11 }} tickFormatter={fmtCost} width={60} />
          <Tooltip
            contentStyle={tooltipStyle}
            formatter={(value, name) =>
              name === 'total_cost'
                ? ([fmtCost(Number(value)), 'Cost'] as [string, string])
                : ([fmtCount(Number(value)), 'Calls'] as [string, string])
            }
          />
          <Area
            type="monotone"
            dataKey="total_cost"
            stroke="#56d364"
            strokeWidth={2}
            fill="url(#costGrad)"
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}

function KpiCard({
  label,
  value,
  accent,
  warn,
}: {
  label: string;
  value: string;
  accent?: boolean;
  warn?: boolean;
}) {
  return (
    <div className={`cost-kpi${accent ? ' accent' : ''}${warn ? ' warn' : ''}`}>
      <div className="cost-kpi-value">{value}</div>
      <div className="cost-kpi-label">{label}</div>
    </div>
  );
}

const tooltipStyle: React.CSSProperties = {
  background: '#1a1a1a',
  border: '1px solid #444',
  borderRadius: 6,
  fontSize: 12,
  color: '#e0e0e0',
};
