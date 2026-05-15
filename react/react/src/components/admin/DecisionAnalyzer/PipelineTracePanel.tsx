import { useMemo, useState } from 'react';
import { ChevronDown, ChevronRight } from 'lucide-react';
import type {
  InterventionOperation,
  InterventionTrace,
  StrategyPipelineSnapshot,
} from './types';
import './PipelineTracePanel.css';

interface PipelineTracePanelProps {
  trace: InterventionTrace[];
  snapshot: StrategyPipelineSnapshot | null;
  actionTaken: string | null;
}

// no_op: muted; suggest/adjust: informational; clamp: caution;
// override/veto: high-attention. Matches the semantic severity in the
// InterventionTrace dataclass docstring (poker/strategy/intervention_trace.py).
const OPERATION_TONE: Record<InterventionOperation, string> = {
  no_op: 'muted',
  suggest: 'info',
  adjust: 'info',
  clamp: 'warn',
  override: 'alert',
  veto: 'alert',
};

function formatActionTransition(t: InterventionTrace): string {
  const before = t.primary_action_before || '∅';
  const after = t.primary_action_after || '∅';
  const beforeBucket = t.amount_bucket_before ? ` (${t.amount_bucket_before})` : '';
  const afterBucket = t.amount_bucket_after ? ` (${t.amount_bucket_after})` : '';
  if (before === after && beforeBucket === afterBucket) {
    return `${before}${beforeBucket}`;
  }
  return `${before}${beforeBucket} → ${after}${afterBucket}`;
}

function StrategyDiff({
  before,
  after,
}: {
  before: Record<string, number>;
  after: Record<string, number>;
}) {
  const keys = useMemo(() => {
    const all = new Set([...Object.keys(before), ...Object.keys(after)]);
    return Array.from(all).sort();
  }, [before, after]);

  if (keys.length === 0) return null;

  return (
    <table className="trace-diff">
      <thead>
        <tr>
          <th>Action</th>
          <th>Before</th>
          <th>After</th>
          <th>Δ</th>
        </tr>
      </thead>
      <tbody>
        {keys.map((k) => {
          const b = before[k] ?? 0;
          const a = after[k] ?? 0;
          const delta = a - b;
          const deltaClass = delta > 0.001 ? 'positive' : delta < -0.001 ? 'negative' : 'neutral';
          return (
            <tr key={k}>
              <td className="trace-diff-action">{k}</td>
              <td>{b.toFixed(3)}</td>
              <td>{a.toFixed(3)}</td>
              <td className={deltaClass}>
                {delta >= 0 ? '+' : ''}
                {delta.toFixed(3)}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

function TraceRow({ trace }: { trace: InterventionTrace }) {
  const [expanded, setExpanded] = useState(false);
  const tone = OPERATION_TONE[trace.operation] ?? 'muted';
  const dimmed = !trace.fired;

  return (
    <>
      <tr
        className={`trace-row tone-${tone} ${dimmed ? 'dimmed' : ''}`}
        onClick={() => setExpanded((v) => !v)}
      >
        <td className="trace-toggle">
          {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        </td>
        <td className="trace-layer">
          <span className="trace-layer-name">{trace.layer}</span>
          <span className="trace-rule-id">{trace.rule_id}</span>
        </td>
        <td>
          <span className={`trace-op-chip tone-${tone}`}>{trace.operation}</span>
        </td>
        <td className="trace-action">{formatActionTransition(trace)}</td>
        <td className="trace-effect">{trace.effect_size.toFixed(3)}</td>
        <td className="trace-reason">{trace.reason_code || '—'}</td>
      </tr>
      {expanded && (
        <tr className="trace-row-detail">
          <td />
          <td colSpan={5}>
            {trace.rationale && <p className="trace-rationale">{trace.rationale}</p>}
            <StrategyDiff
              before={trace.input_strategy_summary}
              after={trace.output_strategy_summary}
            />
            {Object.keys(trace.inputs).length > 0 && (
              <details className="trace-extra">
                <summary>inputs</summary>
                <pre>{JSON.stringify(trace.inputs, null, 2)}</pre>
              </details>
            )}
            {Object.keys(trace.config_snapshot).length > 0 && (
              <details className="trace-extra">
                <summary>config snapshot</summary>
                <pre>{JSON.stringify(trace.config_snapshot, null, 2)}</pre>
              </details>
            )}
            {Object.keys(trace.extra).length > 0 && (
              <details className="trace-extra">
                <summary>extra</summary>
                <pre>{JSON.stringify(trace.extra, null, 2)}</pre>
              </details>
            )}
          </td>
        </tr>
      )}
    </>
  );
}

// Find the strategy distribution the sampler actually saw. Priority:
//   1. The latest non-empty `output_strategy_summary` from the trace
//      (this is what the LAST layer that modified the strategy produced).
//   2. The latest non-empty `input_strategy_summary` — fallback when a
//      layer recorded only an input.
//   3. `snapshot.base_strategy_probs` — the solver-table baseline,
//      which is the source the sampler operates on when no layers fire
//      (the common "all no_op" case).
function findFinalStrategy(
  sortedTrace: InterventionTrace[],
  snapshot: StrategyPipelineSnapshot | null,
): Record<string, number> | null {
  for (let i = sortedTrace.length - 1; i >= 0; i -= 1) {
    const out = sortedTrace[i].output_strategy_summary;
    if (out && Object.keys(out).length > 0) return out;
  }
  for (let i = sortedTrace.length - 1; i >= 0; i -= 1) {
    const inp = sortedTrace[i].input_strategy_summary;
    if (inp && Object.keys(inp).length > 0) return inp;
  }
  const base = snapshot?.base_strategy_probs;
  if (base && typeof base === 'object' && Object.keys(base).length > 0) {
    return base as Record<string, number>;
  }
  return null;
}

function FinalRollPanel({
  finalStrategy,
  actionTaken,
  sampledAbstract,
  resolvedAction,
  resolvedRaiseTo,
}: {
  finalStrategy: Record<string, number>;
  actionTaken: string | null;
  sampledAbstract: string | null;
  resolvedAction: string | null;
  resolvedRaiseTo: number | null;
}) {
  const entries = useMemo(
    () =>
      Object.entries(finalStrategy)
        .sort(([, a], [, b]) => b - a),
    [finalStrategy],
  );

  // Strategy keys use abstract names (`check`, `bet_67`, `raise_2.5bb`).
  // Prefer `sampledAbstract` for highlighting; fall back to `actionTaken`
  // for older rows that didn't capture the abstract name.
  const matchKey = sampledAbstract ?? actionTaken;
  const sampledProb = matchKey ? finalStrategy[matchKey] : undefined;

  const headlineAction = resolvedAction
    ? resolvedAction.toUpperCase() + (resolvedRaiseTo ? ` $${resolvedRaiseTo}` : '')
    : actionTaken
      ? actionTaken.toUpperCase()
      : '—';

  return (
    <div className="pipeline-final-roll">
      <div className="pipeline-final-roll-header">
        <span className="pipeline-final-roll-label">Sampled</span>
        <span className="pipeline-final-roll-action">{headlineAction}</span>
        {sampledAbstract && sampledAbstract !== resolvedAction && (
          <span className="pipeline-final-roll-abstract">
            (from <code>{sampledAbstract}</code>)
          </span>
        )}
        {sampledProb != null && (
          <span className="pipeline-final-roll-prob">
            {(sampledProb * 100).toFixed(1)}% of the final distribution
          </span>
        )}
      </div>
      <table className="trace-diff pipeline-final-dist">
        <thead>
          <tr>
            <th>Action</th>
            <th>Probability</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {entries.map(([action, prob]) => {
            const isSampled = action === matchKey;
            const pct = prob * 100;
            // 2.4px per 1% → 100% = 240px max bar width.
            const barPx = Math.max(3, pct * 2.4);
            return (
              <tr key={action} className={isSampled ? 'sampled' : ''}>
                <td className="trace-diff-action">{action}</td>
                <td>{pct.toFixed(1)}%</td>
                <td className="pipeline-prob-bar-cell">
                  <span
                    className="pipeline-prob-bar"
                    style={{ width: `${barPx.toFixed(1)}px` }}
                  />
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export function PipelineTracePanel({ trace, snapshot, actionTaken }: PipelineTracePanelProps) {
  const sorted = useMemo(
    () => [...trace].sort((a, b) => a.layer_order - b.layer_order),
    [trace],
  );

  const firedCount = sorted.filter((t) => t.fired).length;
  const finalStrategy = useMemo(
    () => findFinalStrategy(sorted, snapshot),
    [sorted, snapshot],
  );

  return (
    <div className="pipeline-trace-panel">
      <div className="pipeline-trace-header">
        <h4>TieredBot Pipeline</h4>
        <div className="pipeline-trace-meta">
          <span className="pipeline-source-chip">TieredBot</span>
          {actionTaken && (
            <span className={`pipeline-action-chip action-${actionTaken.toLowerCase()}`}>
              {actionTaken.toUpperCase()}
            </span>
          )}
          <span className="pipeline-fire-count">
            {firedCount} / {sorted.length} layers fired
          </span>
        </div>
      </div>

      {finalStrategy && (
        <FinalRollPanel
          finalStrategy={finalStrategy}
          actionTaken={actionTaken}
          sampledAbstract={(snapshot?.sampled_abstract_action as string | undefined) ?? null}
          resolvedAction={(snapshot?.resolved_action as string | undefined) ?? null}
          resolvedRaiseTo={(snapshot?.resolved_raise_to as number | undefined) ?? null}
        />
      )}

      <details className="pipeline-section">
        <summary>Pipeline trace ({firedCount} / {sorted.length} layers fired)</summary>
        <table className="trace-table">
          <thead>
            <tr>
              <th />
              <th>Layer · Rule</th>
              <th>Op</th>
              <th>Action before → after</th>
              <th>Effect</th>
              <th>Reason</th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((t, i) => (
              <TraceRow key={`${t.layer}-${t.rule_id}-${i}`} trace={t} />
            ))}
          </tbody>
        </table>
      </details>

      {snapshot && Object.keys(snapshot).length > 0 && (
        <details className="pipeline-section">
          <summary>Pipeline snapshot</summary>
          <pre className="pipeline-snapshot">{JSON.stringify(snapshot, null, 2)}</pre>
        </details>
      )}
    </div>
  );
}
