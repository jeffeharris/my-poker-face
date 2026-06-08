import type { CaptureStats, DecisionAnalysisStats } from './types';

interface AnalysisStatsBarProps {
  analysisStats: DecisionAnalysisStats;
  stats: CaptureStats | null;
}

// Decision-analysis summary stats: totals, correctness, EV, equity, and
// selected action counts. (Label filtering lives in the filter controls, not
// here.)
export function AnalysisStatsBar({ analysisStats, stats }: AnalysisStatsBarProps) {
  return (
    <div className="debugger-stats analysis-stats">
      <div className="stat-item">
        <span className="stat-value">{analysisStats.total}</span>
        <span className="stat-label">Analyzed</span>
      </div>
      <div className="stat-item stat-success">
        <span className="stat-value">{analysisStats.correct}</span>
        <span className="stat-label">Correct</span>
      </div>
      <div className="stat-item stat-danger">
        <span className="stat-value">{analysisStats.mistakes}</span>
        <span className="stat-label">Mistakes</span>
      </div>
      <div className="stat-item">
        <span className="stat-value">${Math.round(analysisStats.total_ev_lost)}</span>
        <span className="stat-label">EV Lost</span>
      </div>
      {analysisStats.avg_equity !== null && (
        <div className="stat-item">
          <span className="stat-value">{(analysisStats.avg_equity * 100).toFixed(1)}%</span>
          <span className="stat-label">Avg Equity</span>
        </div>
      )}
      {analysisStats.avg_equity_vs_ranges !== null && (
        <div className="stat-item">
          <span className="stat-value">
            {(analysisStats.avg_equity_vs_ranges * 100).toFixed(1)}%
          </span>
          <span className="stat-label">Equity (Ranges)</span>
        </div>
      )}
      {/* Selected action counts row */}
      {stats && (
        <div className="stat-row">
          <div className="stat-item stat-warning">
            <span className="stat-value">{stats.suspicious_folds}</span>
            <span className="stat-label">Sus Folds</span>
          </div>
          <div className="stat-item action-allin">
            <span className="stat-value">{stats.by_action.all_in || 0}</span>
            <span className="stat-label">All In</span>
          </div>
          <div className="stat-item action-call">
            <span className="stat-value">{stats.by_action.call || 0}</span>
            <span className="stat-label">Call</span>
          </div>
          <div className="stat-item action-raise">
            <span className="stat-value">{stats.by_action.raise || 0}</span>
            <span className="stat-label">Raise</span>
          </div>
        </div>
      )}
    </div>
  );
}
