import { useMemo, useState } from 'react';
import './RelationshipMatrix.css';

/**
 * Shape returned by `GET /api/game/<id>/relationships`. Kept narrow to
 * what the matrix actually consumes — additional fields on the API
 * response are ignored. If the backend evolves the contract, update
 * here.
 */
export interface RelationshipMemorableHand {
  hand_id: number;
  event: string;
  impact_score: number;
  narrative: string;
  timestamp: string;
}

export interface RelationshipPair {
  observer: string;
  opponent: string;
  observer_id: string;
  opponent_id: string;
  heat: number;
  respect: number;
  likability: number;
  /** 'rival' | 'friendly' | null (neutral) */
  label: string | null;
  last_seen: string | null;
  memorable_hands: RelationshipMemorableHand[];
}

export interface RelationshipsPayload {
  game_id: string;
  pair_count: number;
  now: string;
  pairs: RelationshipPair[];
}

interface RelationshipMatrixProps {
  data: RelationshipsPayload;
}

interface PairKey {
  observer: string;
  opponent: string;
}

/**
 * Matrix view: rows = observer's POV, columns = the opponent being viewed.
 * Diagonal cells (self-pair) are omitted. Color cell background by label;
 * triple-bar inside encodes heat / respect / likability. Click a cell to
 * surface the pair's memorable hands + numeric axis values.
 *
 * The matrix is asymmetric on purpose — `(Batman → Joker)` is a
 * different read than `(Joker → Batman)`, and the bilateral update
 * pattern means both rows are independently meaningful. Visual
 * asymmetry across the diagonal is the headline signal here.
 */
export function RelationshipMatrix({ data }: RelationshipMatrixProps) {
  // Stable player ordering: collect every name that appears as
  // observer OR opponent so the matrix doesn't drop seats that have
  // only been talked-about, never talked-from.
  const players = useMemo(() => {
    const seen = new Set<string>();
    for (const pair of data.pairs) {
      seen.add(pair.observer);
      seen.add(pair.opponent);
    }
    return Array.from(seen).sort();
  }, [data.pairs]);

  // Pair lookup keyed on (observer, opponent). The endpoint returns
  // both directions of each pair so the matrix is fully populated.
  const pairsByKey = useMemo(() => {
    const map = new Map<string, RelationshipPair>();
    for (const pair of data.pairs) {
      map.set(`${pair.observer}\x00${pair.opponent}`, pair);
    }
    return map;
  }, [data.pairs]);

  const [selectedKey, setSelectedKey] = useState<PairKey | null>(null);
  const selectedPair = selectedKey
    ? pairsByKey.get(`${selectedKey.observer}\x00${selectedKey.opponent}`)
    : null;

  if (players.length === 0) {
    return (
      <div className="rmx-empty">
        No relationship data yet. Pairs appear once `OpponentModel` rows
        exist for the game.
      </div>
    );
  }

  return (
    <div className="rmx-container">
      <table className="rmx-grid">
        <thead>
          <tr>
            {/* Top-left corner cell — observer-axis label on the row
                header, target-axis label on the column header. The
                small text keeps the corner from competing with the
                player names. */}
            <th className="rmx-corner">
              <span className="rmx-corner-row">observer ↓</span>
              <span className="rmx-corner-col">target →</span>
            </th>
            {players.map(name => (
              <th key={`col-${name}`} className="rmx-header rmx-header-col">
                {name}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {players.map(observer => (
            <tr key={`row-${observer}`}>
              <th className="rmx-header rmx-header-row">{observer}</th>
              {players.map(opponent => {
                if (observer === opponent) {
                  // Self-pair: render a muted diagonal cell so the
                  // grid stays rectangular.
                  return (
                    <td key={`${observer}-${opponent}`} className="rmx-cell rmx-self">
                      <span className="rmx-self-dash">—</span>
                    </td>
                  );
                }
                const pair = pairsByKey.get(`${observer}\x00${opponent}`);
                const labelClass = pair?.label
                  ? `rmx-cell-${pair.label}`
                  : 'rmx-cell-neutral';
                const isSelected =
                  selectedKey?.observer === observer
                  && selectedKey?.opponent === opponent;
                return (
                  <td
                    key={`${observer}-${opponent}`}
                    className={`rmx-cell ${labelClass} ${isSelected ? 'rmx-cell-selected' : ''}`}
                    onClick={() => setSelectedKey({ observer, opponent })}
                    title={pair ? formatTooltip(pair) : 'No data'}
                  >
                    {pair ? <CellBars pair={pair} /> : <span className="rmx-cell-empty">·</span>}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>

      {selectedPair && (
        <DetailPanel
          pair={selectedPair}
          onClose={() => setSelectedKey(null)}
        />
      )}
    </div>
  );
}

/** Three thin axis bars stacked inside a cell. Heat top, respect mid,
 *  likability bottom. Each bar fills proportionally to its 0-1 axis
 *  value; baseline tick at 0.5 reminds the reader where neutral is
 *  for the respect/likability axes (heat's default is 0, not 0.5).
 */
function CellBars({ pair }: { pair: RelationshipPair }) {
  return (
    <div className="rmx-bars">
      <AxisBar label="H" value={pair.heat} colorClass="rmx-bar-heat" />
      <AxisBar label="R" value={pair.respect} colorClass="rmx-bar-respect" />
      <AxisBar label="L" value={pair.likability} colorClass="rmx-bar-likability" />
    </div>
  );
}

function AxisBar({
  label, value, colorClass,
}: {
  label: string;
  value: number;
  colorClass: string;
}) {
  const pct = Math.max(0, Math.min(100, value * 100));
  return (
    <div className="rmx-bar-row">
      <span className="rmx-bar-label">{label}</span>
      <div className="rmx-bar-track">
        <div
          className={`rmx-bar-fill ${colorClass}`}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}

function formatTooltip(pair: RelationshipPair): string {
  const label = pair.label ?? 'neutral';
  return (
    `${pair.observer} → ${pair.opponent}: ${label}\n`
    + `heat ${pair.heat.toFixed(2)}  respect ${pair.respect.toFixed(2)}  likability ${pair.likability.toFixed(2)}`
  );
}

/** Side panel that surfaces the selected pair's full state plus its
 *  memorable hands. Close button returns to the matrix-only view.
 */
function DetailPanel({
  pair, onClose,
}: {
  pair: RelationshipPair;
  onClose: () => void;
}) {
  const label = pair.label ?? 'neutral';
  return (
    <div className="rmx-detail" data-testid="rmx-detail">
      <div className="rmx-detail-header">
        <div className="rmx-detail-title">
          <span className="rmx-detail-observer">{pair.observer}</span>
          <span className="rmx-detail-arrow">→</span>
          <span className="rmx-detail-target">{pair.opponent}</span>
        </div>
        <button
          className="rmx-detail-close"
          onClick={onClose}
          type="button"
          aria-label="Close detail panel"
        >
          ×
        </button>
      </div>
      <div className={`rmx-detail-label rmx-cell-${label}`}>{label}</div>
      <dl className="rmx-detail-axes">
        <div>
          <dt>Heat</dt>
          <dd>{pair.heat.toFixed(3)}</dd>
        </div>
        <div>
          <dt>Respect</dt>
          <dd>{pair.respect.toFixed(3)}</dd>
        </div>
        <div>
          <dt>Likability</dt>
          <dd>{pair.likability.toFixed(3)}</dd>
        </div>
      </dl>
      {pair.last_seen && (
        <div className="rmx-detail-meta">
          Last activity: {new Date(pair.last_seen).toLocaleString()}
        </div>
      )}
      <h4 className="rmx-detail-section">Memorable hands</h4>
      {pair.memorable_hands.length === 0 ? (
        <div className="rmx-detail-empty">None yet.</div>
      ) : (
        <ul className="rmx-detail-hands">
          {pair.memorable_hands.map((h, i) => (
            <li key={`${h.hand_id}-${i}`}>
              <div className="rmx-hand-row">
                <span className="rmx-hand-event">{h.event}</span>
                <span className="rmx-hand-impact">
                  impact {h.impact_score.toFixed(2)}
                </span>
              </div>
              <div className="rmx-hand-narrative">{h.narrative}</div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
