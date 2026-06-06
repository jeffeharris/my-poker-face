import type { DecisionAnalysis } from './types';
import { formatPosition, pairOpponentSeats, safeJsonParse } from './utils';

interface DecisionAnalysisSectionProps {
  analysis: DecisionAnalysis;
}

// Decision Analysis — equity/EV grid shared between mobile and desktop.
export function DecisionAnalysisSection({ analysis }: DecisionAnalysisSectionProps) {
  return (
    <div
      className={`decision-analysis ${analysis.decision_quality === 'mistake' ? 'mistake' : analysis.decision_quality === 'correct' ? 'correct' : ''}`}
    >
      <h4>Decision Analysis</h4>
      <div className="analysis-grid">
        {(analysis.player_position || analysis.opponent_positions) && (
          <div className="analysis-item analysis-item--full">
            <label>Players in hand:</label>
            <span>
              {analysis.player_position && (
                <>
                  <strong>{formatPosition(analysis.player_position)}</strong>
                  {' ('}
                  {analysis.player_name}
                  {' — acting)'}
                </>
              )}
              {(() => {
                const seats = pairOpponentSeats(
                  analysis.opponent_positions,
                  analysis.opponent_ranges_json
                );
                if (seats.length === 0) return null;
                return (
                  <>
                    {analysis.player_position && '; '}
                    {seats.map((s, i) => (
                      <span key={i}>
                        {i > 0 && ', '}
                        {formatPosition(s.position)}
                        {s.name && ` (${s.name})`}
                      </span>
                    ))}
                  </>
                );
              })()}
            </span>
          </div>
        )}
        {analysis.equity != null && (
          <div className="analysis-item">
            <label>Equity:</label>
            <span>{(analysis.equity * 100).toFixed(1)}%</span>
          </div>
        )}
        {analysis.equity_vs_ranges != null && (
          <div className="analysis-item">
            <label>Equity vs Ranges:</label>
            <span>
              {(analysis.equity_vs_ranges * 100).toFixed(1)}%
              {analysis.opponent_positions && (
                <span className="opponent-positions">
                  {' '}
                  (vs{' '}
                  {safeJsonParse<string[]>(analysis.opponent_positions, [])
                    .map(formatPosition)
                    .join(', ')}
                  )
                </span>
              )}
            </span>
          </div>
        )}
        {analysis.required_equity != null && (
          <div className="analysis-item">
            <label>Required Equity:</label>
            <span>{(analysis.required_equity * 100).toFixed(1)}%</span>
          </div>
        )}
        {analysis.ev_call != null && (
          <div className="analysis-item">
            <label>EV (Call):</label>
            <span className={analysis.ev_call >= 0 ? 'positive' : 'negative'}>
              {analysis.ev_call >= 0 ? '+' : ''}${analysis.ev_call.toFixed(0)}
            </span>
          </div>
        )}
        {analysis.optimal_action && (
          <div className="analysis-item">
            <label>Optimal Action:</label>
            <span className={`optimal-action ${analysis.optimal_action}`}>
              {analysis.optimal_action.toUpperCase()}
            </span>
          </div>
        )}
        {analysis.decision_quality && (
          <div className="analysis-item quality">
            <label>Quality:</label>
            <span className={`quality-badge ${analysis.decision_quality}`}>
              {analysis.decision_quality.toUpperCase()}
            </span>
          </div>
        )}
        {analysis.ev_lost != null && analysis.ev_lost > 0 && (
          <div className="analysis-item">
            <label>EV Lost:</label>
            <span className="negative">-${analysis.ev_lost.toFixed(0)}</span>
          </div>
        )}
      </div>
    </div>
  );
}
