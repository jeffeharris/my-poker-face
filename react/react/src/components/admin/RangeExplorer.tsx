import { useEffect, useState, useCallback } from 'react';
import { adminAPI } from '../../utils/api';
import { logger } from '../../utils/logger';
import './RangeExplorer.css';

// 13x13 grid layout: ranks high→low. Suited hands sit in the upper-right
// triangle, pocket pairs on the diagonal, offsuit in the lower-left.
const RANKS = ['A', 'K', 'Q', 'J', 'T', '9', '8', '7', '6', '5', '4', '3', '2'];

function canonFor(i: number, j: number): string {
  if (i === j) return RANKS[i] + RANKS[i];
  if (i < j) return RANKS[i] + RANKS[j] + 's'; // higher rank first, suited
  return RANKS[j] + RANKS[i] + 'o'; // higher rank first, offsuit
}

type Cell = [number, number]; // [vpip, total]

interface GridCell {
  canon: string;
  vpip: number;
  total: number;
  pct: number | null;
}

interface PlayerRow {
  player: string;
  vpip: number;
  total: number;
  pct: number | null;
}

interface ClassRow {
  tier: string;
  vpip: number;
  total: number;
  pct: number | null;
}

interface Filters {
  controllers: string[];
  archetypes: string[];
  positions: string[];
  players: string[];
  sources: string[];
  modes: string[];
}

interface Summary {
  vpip: number;
  total: number;
  pct: number | null;
  decisions: number;
}

interface GridResponse {
  filters: Filters;
  applied: Record<string, string>;
  grid: GridCell[];
  by_class: ClassRow[];
  by_player: PlayerRow[];
  min_player_n: number;
  summary: Summary;
}

interface MatrixPlayer {
  player: string;
  vpip: number;
  total: number;
  pct: number | null;
  cells: Record<string, Cell>;
}

interface MatrixResponse {
  order: string[];
  aggregate: Record<string, Cell>;
  players: MatrixPlayer[];
  min_player_n: number;
  filters: Filters;
  applied: Record<string, string>;
  summary: Summary;
}

interface RangeExplorerProps {
  embedded?: boolean;
}

type View = 'grid' | 'class' | 'matrix';
type Metric = 'vpip' | 'count';

const HEAT_VPIP = '212, 165, 116'; // gold — VPIP %
const HEAT_COUNT = '120, 170, 230'; // blue — hands dealt

// frac in [0,1]; null = no data (transparent).
function heat(frac: number | null, rgb: string): React.CSSProperties {
  if (frac === null) {
    return { background: 'transparent', color: 'var(--text-muted, #555)' };
  }
  const clamped = Math.max(0, Math.min(1, frac));
  const a = 0.08 + clamped * 0.92;
  return {
    background: `rgba(${rgb}, ${a.toFixed(3)})`,
    color: clamped > 0.55 ? '#1a1a1a' : 'var(--text-primary, #e0e0e0)',
  };
}

function pctOf(cell: Cell | undefined): number | null {
  if (!cell || !cell[1]) return null;
  return (cell[0] / cell[1]) * 100;
}

function maxTotal(cells: Iterable<Cell>): number {
  let m = 0;
  for (const c of cells) m = Math.max(m, c[1]);
  return m;
}

const PHASES = ['PRE_FLOP', 'FLOP', 'TURN', 'RIVER'];

export function RangeExplorer({ embedded = false }: RangeExplorerProps) {
  const [grid, setGrid] = useState<GridResponse | null>(null);
  const [matrix, setMatrix] = useState<MatrixResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [view, setView] = useState<View>('grid');
  const [metric, setMetric] = useState<Metric>('vpip');
  const [phase, setPhase] = useState('PRE_FLOP');
  const [controller, setController] = useState('');
  const [archetype, setArchetype] = useState('');
  const [position, setPosition] = useState('');
  const [player, setPlayer] = useState('');
  const [source, setSource] = useState('');
  const [gameMode, setGameMode] = useState('');

  const fetchData = useCallback(async () => {
    setLoading(true);
    setError(null);
    const qs = new URLSearchParams();
    qs.set('phase', phase);
    if (controller) qs.set('controller', controller);
    if (archetype) qs.set('archetype', archetype);
    if (position) qs.set('position', position);
    if (source) qs.set('source', source);
    if (gameMode) qs.set('mode', gameMode);
    try {
      if (view === 'matrix') {
        const resp = await adminAPI.fetch(`/api/admin/range-explorer/matrix?${qs.toString()}`);
        if (!resp.ok) throw new Error(`Matrix returned ${resp.status}`);
        setMatrix(await resp.json());
      } else {
        if (player) qs.set('player', player);
        const resp = await adminAPI.fetch(`/api/admin/range-explorer/grid?${qs.toString()}`);
        if (!resp.ok) throw new Error(`Grid returned ${resp.status}`);
        setGrid(await resp.json());
      }
    } catch (err) {
      logger.error('Failed to load range data:', err);
      setError(err instanceof Error ? err.message : 'Failed to load range data');
    } finally {
      setLoading(false);
    }
  }, [view, phase, controller, archetype, position, player, source, gameMode]);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  // Filter options + summary come from whichever response backs the active
  // view; fall back to the other so the dropdowns survive a view switch.
  const active = view === 'matrix' ? matrix : grid;
  const opts: Filters | undefined = (active ?? grid ?? matrix)?.filters;
  const summary = active?.summary;

  const isCount = metric === 'count';
  const rgb = isCount ? HEAT_COUNT : HEAT_VPIP;

  const cellByCanon = new Map((grid?.grid ?? []).map((c) => [c.canon, c]));
  const gridMax = Math.max(1, ...(grid?.grid ?? []).map((c) => c.total));
  const classMax = Math.max(1, ...(grid?.by_class ?? []).map((c) => c.total));

  return (
    <div className={`range-explorer${embedded ? ' embedded' : ''}`}>
      <div className="re-header">
        <div>
          <h2 className="re-title">Range Explorer</h2>
          <p className="re-subtitle">
            VPIP by starting hand — who played what, and which bot decided. Big-blind free-checks
            excluded. Decisions deduped per hand.
          </p>
        </div>
        <div className="re-header-right">
          <div className="re-viewtoggle">
            <button
              type="button"
              className={view === 'grid' ? 'active' : ''}
              onClick={() => setView('grid')}
            >
              Grid
            </button>
            <button
              type="button"
              className={view === 'class' ? 'active' : ''}
              onClick={() => setView('class')}
            >
              By class
            </button>
            <button
              type="button"
              className={view === 'matrix' ? 'active' : ''}
              onClick={() => setView('matrix')}
            >
              Players
            </button>
          </div>
          <div className="re-viewtoggle">
            <button
              type="button"
              className={metric === 'vpip' ? 'active' : ''}
              onClick={() => setMetric('vpip')}
            >
              VPIP %
            </button>
            <button
              type="button"
              className={metric === 'count' ? 'active' : ''}
              onClick={() => setMetric('count')}
            >
              Count
            </button>
          </div>
          {summary && (
            <div className="re-summary">
              <span className="re-summary-pct">
                {summary.pct === null ? '—' : `${summary.pct}%`}
              </span>
              <span className="re-summary-label">
                VPIP · {summary.decisions.toLocaleString()} decisions
              </span>
            </div>
          )}
        </div>
      </div>

      <div className="re-filters">
        <label>
          Phase
          <select value={phase} onChange={(e) => setPhase(e.target.value)}>
            {PHASES.map((p) => (
              <option key={p} value={p}>
                {p}
              </option>
            ))}
          </select>
        </label>
        <label>
          Game mode
          <select value={gameMode} onChange={(e) => setGameMode(e.target.value)}>
            <option value="">All</option>
            {(opts?.modes ?? []).map((m) => (
              <option key={m} value={m}>
                {m === 'tournament' ? 'Tournament' : 'Cash'}
              </option>
            ))}
          </select>
        </label>
        <label>
          Source
          <select value={source} onChange={(e) => setSource(e.target.value)}>
            <option value="">All games</option>
            {(opts?.sources ?? []).map((s) => (
              <option key={s} value={s}>
                {s === 'human' ? 'Human-played' : s === 'experiment' ? 'Experiments' : 'Other'}
              </option>
            ))}
          </select>
        </label>
        <label>
          Bot
          <select
            value={controller}
            onChange={(e) => {
              setController(e.target.value);
              setPlayer('');
            }}
          >
            <option value="">All AI</option>
            {(opts?.controllers ?? []).map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>
        </label>
        <label>
          Archetype
          <select value={archetype} onChange={(e) => setArchetype(e.target.value)}>
            <option value="">All</option>
            {(opts?.archetypes ?? []).map((a) => (
              <option key={a} value={a}>
                {a}
              </option>
            ))}
          </select>
        </label>
        <label>
          Position
          <select value={position} onChange={(e) => setPosition(e.target.value)}>
            <option value="">All</option>
            {(opts?.positions ?? []).map((p) => (
              <option key={p} value={p}>
                {p}
              </option>
            ))}
          </select>
        </label>
        {view !== 'matrix' && (
          <label>
            Player
            <select value={player} onChange={(e) => setPlayer(e.target.value)}>
              <option value="">All players</option>
              {(opts?.players ?? []).map((p) => (
                <option key={p} value={p}>
                  {p}
                </option>
              ))}
            </select>
          </label>
        )}
        {(controller || archetype || position || player || source || gameMode) && (
          <button
            type="button"
            className="re-clear"
            onClick={() => {
              setController('');
              setArchetype('');
              setPosition('');
              setPlayer('');
              setSource('');
              setGameMode('');
            }}
          >
            Clear
          </button>
        )}
      </div>

      {error && <div className="re-error">{error}</div>}
      {loading && !active && <div className="re-loading">Loading…</div>}

      {/* Grid + class views */}
      {view !== 'matrix' && grid && (
        <div className="re-body">
          <div className="re-grid-wrap">
            {view === 'grid' && (
              <>
                <table className="re-grid">
                  <tbody>
                    {RANKS.map((r1, i) => (
                      <tr key={r1}>
                        {RANKS.map((r2, j) => {
                          const canon = canonFor(i, j);
                          const cell = cellByCanon.get(canon);
                          const frac = !cell
                            ? null
                            : isCount
                              ? cell.total / gridMax
                              : cell.pct === null
                                ? null
                                : cell.pct / 100;
                          const label = !cell
                            ? ''
                            : isCount
                              ? cell.total
                              : cell.pct === null
                                ? ''
                                : Math.round(cell.pct);
                          return (
                            <td
                              key={r2}
                              className={`re-cell${i === j ? ' pair' : ''}`}
                              style={heat(frac, rgb)}
                              title={
                                cell
                                  ? `${canon}: ${cell.vpip}/${cell.total} VPIP`
                                  : `${canon}: no data`
                              }
                            >
                              <span className="re-cell-hand">{canon}</span>
                              <span className="re-cell-pct">{label}</span>
                            </td>
                          );
                        })}
                      </tr>
                    ))}
                  </tbody>
                </table>
                <div className="re-legend">
                  <span>{isCount ? 'fewer' : 'tight'}</span>
                  <div className={`re-legend-bar${isCount ? ' count' : ''}`} />
                  <span>{isCount ? 'more' : 'loose'}</span>
                  <span className="re-legend-note">
                    cell = {isCount ? '# hands dealt' : 'VPIP %'} · suited ▲ · pairs ╲ · offsuit ▼
                  </span>
                </div>
              </>
            )}
            {view === 'class' && (
              <div className="re-classes">
                {grid.by_class.map((cls) => {
                  const frac = isCount
                    ? cls.total / classMax
                    : cls.pct === null
                      ? null
                      : cls.pct / 100;
                  return (
                    <div
                      key={cls.tier}
                      className="re-class-cell"
                      style={heat(frac, rgb)}
                      title={`${cls.tier}: ${cls.vpip}/${cls.total} VPIP`}
                    >
                      <span className="re-class-name">{cls.tier}</span>
                      <span className="re-class-pct">
                        {isCount
                          ? cls.total.toLocaleString()
                          : cls.pct === null
                            ? '—'
                            : `${cls.pct}%`}
                      </span>
                      <span className="re-class-n">
                        {isCount ? `${cls.vpip.toLocaleString()} vpip` : cls.total.toLocaleString()}
                      </span>
                    </div>
                  );
                })}
                {grid.by_class.length === 0 && (
                  <div className="re-loading">No classed hands in this cut.</div>
                )}
              </div>
            )}
          </div>

          <div className="re-players">
            <div className="re-players-head">
              By player · ≥{grid.min_player_n} hands{grid.applied.player ? '' : ' · click to focus'}
            </div>
            <div className="re-players-list">
              {grid.by_player.map((row) => (
                <button
                  key={row.player}
                  type="button"
                  className={`re-player-row${grid.applied.player === row.player ? ' active' : ''}`}
                  onClick={() => setPlayer(grid.applied.player === row.player ? '' : row.player)}
                >
                  <span className="re-player-name">{row.player}</span>
                  <span className="re-player-bar-wrap">
                    <span className="re-player-bar" style={{ width: `${row.pct ?? 0}%` }} />
                  </span>
                  <span className="re-player-pct">{row.pct === null ? '—' : `${row.pct}%`}</span>
                  <span className="re-player-n">{row.total}</span>
                </button>
              ))}
            </div>
          </div>
        </div>
      )}

      {/* Players matrix view: one heatmap row per player, hands strength-ordered */}
      {view === 'matrix' && matrix && (
        <div className="re-matrix">
          <div className="re-matrix-axis">
            <span>← stronger</span>
            <span className="re-matrix-axis-note">
              {matrix.order.length} hands by eval7 all-in equity · ≥{matrix.min_player_n}{' '}
              hands/player · {isCount ? 'shade = # dealt (per-row scale)' : 'shade = VPIP %'} ·
              hover for detail
            </span>
            <span>weaker →</span>
          </div>
          <div className="re-matrix-scroll">
            <div className="re-matrix-row aggregate">
              <span className="re-matrix-label">All players</span>
              <div className="re-matrix-cells">
                {(() => {
                  const aggMax = Math.max(
                    1,
                    ...matrix.order.map((c) => matrix.aggregate[c]?.[1] ?? 0)
                  );
                  return matrix.order.map((canon) => {
                    const c = matrix.aggregate[canon];
                    const frac = !c ? null : isCount ? c[1] / aggMax : pctOf(c);
                    const v = frac === null ? null : isCount ? frac : frac / 100;
                    return (
                      <span
                        key={canon}
                        className="re-matrix-cell"
                        style={heat(v, rgb)}
                        title={c ? `${canon}: ${c[0]}/${c[1]} VPIP` : `${canon}: no data`}
                      />
                    );
                  });
                })()}
              </div>
            </div>
            {matrix.players.map((p) => {
              const rowMax = Math.max(1, maxTotal(Object.values(p.cells)));
              return (
                <div key={p.player} className="re-matrix-row">
                  <span
                    className="re-matrix-label"
                    title={`${p.player} · ${p.pct}% VPIP (n=${p.total})`}
                  >
                    {p.player}
                  </span>
                  <div className="re-matrix-cells">
                    {matrix.order.map((canon) => {
                      const c = p.cells[canon];
                      const pct = pctOf(c);
                      const v = !c
                        ? null
                        : isCount
                          ? c[1] / rowMax
                          : pct === null
                            ? null
                            : pct / 100;
                      return (
                        <span
                          key={canon}
                          className="re-matrix-cell"
                          style={heat(v, rgb)}
                          title={
                            c ? `${p.player} ${canon}: ${c[0]}/${c[1]} VPIP` : `${canon}: no data`
                          }
                        />
                      );
                    })}
                  </div>
                </div>
              );
            })}
            {matrix.players.length === 0 && (
              <div className="re-loading">No players meet the sample threshold in this cut.</div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
