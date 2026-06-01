/**
 * CoachEffectivenessPanel — admin view of "is the live coach helping?".
 *
 * Reads GET /api/coach/metrics/tip-effectiveness (global): after a preflop leak
 * nudge fired in-game, how often did the player's next decision follow the
 * solver line? Joins coach_tips ⋈ player_decision_analysis. Empty until coached
 * games have been played — the table only fills as nudges accrue.
 */

import { useCallback, useEffect, useState } from 'react';
import { GraduationCap, RefreshCw } from 'lucide-react';
import { adminAPI } from '../../utils/api';
import { logger } from '../../utils/logger';
import './CoachEffectivenessPanel.css';

interface KindEffect {
  nudges: number;
  followed: number;
  follow_rate: number | null;
}
interface Effectiveness {
  by_kind: Record<string, KindEffect>;
  overall: KindEffect;
}

const KIND_LABEL: Record<string, string> = {
  limp: 'Limping',
  too_loose: 'Too loose',
  over_fold: 'Over-folding',
  too_passive: 'Too passive',
};
// What "followed the solver" means for each leak kind (matches the backend).
const KIND_TARGET: Record<string, string> = {
  limp: 'raised or folded (not a limp)',
  too_loose: 'folded the off-range hand',
  over_fold: 'continued instead of folding',
  too_passive: 'raised instead of flat-calling',
};

const pct = (x: number | null) => (x == null ? '—' : `${Math.round(x * 100)}%`);

interface Props {
  embedded?: boolean;
}

export function CoachEffectivenessPanel({ embedded = false }: Props) {
  const [data, setData] = useState<Effectiveness | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const resp = await adminAPI.fetch('/api/coach/metrics/tip-effectiveness');
      if (!resp.ok) throw new Error(`effectiveness ${resp.status}`);
      setData(await resp.json());
    } catch (err) {
      logger.error('Failed to load coach effectiveness:', err);
      setError('Could not load coach effectiveness.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const overall = data?.overall;
  const kinds = data ? Object.entries(data.by_kind) : [];

  return (
    <div className={embedded ? 'cep cep--embedded' : 'cep'}>
      <div className="cep-header">
        <div>
          <h2 className="cep-title">
            <GraduationCap size={20} /> Coach follow-through
          </h2>
          <p className="cep-sub">
            After a preflop leak nudge fired in-game, how often did the player take the
            solver line on that decision? (all players)
          </p>
        </div>
        <button className="cep-refresh" onClick={load} disabled={loading} title="Refresh">
          <RefreshCw size={16} />
        </button>
      </div>

      {loading && <div className="cep-state">Loading…</div>}
      {error && <div className="cep-state cep-error">{error}</div>}

      {data && !loading && !error && (
        <>
          {overall && overall.nudges > 0 ? (
            <>
              <div className="cep-overall">
                <div className="cep-rate">{pct(overall.follow_rate)}</div>
                <div className="cep-overall-detail">
                  followed the solver in{' '}
                  <strong>
                    {overall.followed}/{overall.nudges}
                  </strong>{' '}
                  nudged spots
                </div>
              </div>

              <table className="cep-table">
                <thead>
                  <tr>
                    <th>Leak</th>
                    <th>Nudges</th>
                    <th>Followed</th>
                    <th>Rate</th>
                    <th>“Followed” = </th>
                  </tr>
                </thead>
                <tbody>
                  {kinds.map(([kind, e]) => (
                    <tr key={kind}>
                      <td>{KIND_LABEL[kind] ?? kind}</td>
                      <td>{e.nudges}</td>
                      <td>{e.followed}</td>
                      <td>{pct(e.follow_rate)}</td>
                      <td className="cep-target">{KIND_TARGET[kind] ?? '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          ) : (
            <div className="cep-state">
              <GraduationCap size={28} />
              <p>
                No coached nudges logged yet. Once players see preflop leak nudges in-game
                (proactive coach mode), their follow-through shows up here.
              </p>
            </div>
          )}
          <p className="cep-note">
            Compliance only — this measures what players did after a nudge, not yet a
            nudged-vs-not causal comparison.
          </p>
        </>
      )}
    </div>
  );
}
