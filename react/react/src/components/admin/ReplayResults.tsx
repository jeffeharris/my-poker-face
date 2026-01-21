import { useState, useEffect, useCallback } from 'react';
import { adminAPI } from '../../utils/api';
import {
  BarChart3, TrendingUp, TrendingDown, Minus, AlertCircle,
  RefreshCw, ChevronDown, ChevronUp, ArrowRight, Clock, Zap
} from 'lucide-react';
import './AdminShared.css';
import './ReplayResults.css';

// ============================================
// Types
// ============================================

interface ReplayExperiment {
  id: number;
  name: string;
  description?: string;
  hypothesis?: string;
  status: string;
  created_at: string;
  capture_count: number;
  variant_count: number;
  results_completed: number;
  results_total: number;
}

interface VariantStats {
  total: number;
  actions_changed: number;
  improved: number;
  degraded: number;
  avg_ev_delta: number | null;
  avg_latency: number | null;
  total_input_tokens: number;
  total_output_tokens: number;
  errors: number;
}

interface Summary {
  overall: VariantStats;
  by_variant: Record<string, VariantStats>;
}

interface ReplayResult {
  id: number;
  capture_id: number;
  variant: string;
  new_action: string;
  new_quality?: string;
  action_changed: boolean;
  quality_change?: string;
  ev_delta?: number;
  latency_ms: number;
  model: string;
  provider: string;
  player_name: string;
  phase: string;
  pot_odds?: number;
  original_action: string;
  original_quality?: string;
}

interface ReplayResultsProps {
  experimentId: number;
  onBack?: () => void;
}

// ============================================
// Main Component
// ============================================

export function ReplayResults({ experimentId, onBack: _onBack }: ReplayResultsProps) {
  const [experiment, setExperiment] = useState<ReplayExperiment | null>(null);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [results, setResults] = useState<ReplayResult[]>([]);
  const [loading, setLoading] = useState(true);
  const [_error, setError] = useState<string | null>(null);

  // Filters
  const [variantFilter, setVariantFilter] = useState<string>('');
  const [qualityFilter, setQualityFilter] = useState<string>('');
  const [showDetails, setShowDetails] = useState(false);

  // Polling for running experiments
  const [isPolling, setIsPolling] = useState(false);

  const fetchData = useCallback(async () => {
    try {
      // Fetch experiment
      const expResponse = await adminAPI.fetch(`/api/replay-experiments/${experimentId}`);
      const expData = await expResponse.json();
      if (expData.success) {
        setExperiment(expData.experiment);
        // Enable polling if running
        setIsPolling(expData.experiment.status === 'running');
      }

      // Fetch summary
      const summaryResponse = await adminAPI.fetch(`/api/replay-experiments/${experimentId}/summary`);
      const summaryData = await summaryResponse.json();
      if (summaryData.success) {
        setSummary(summaryData.summary);
      }

      // Fetch results
      const params = new URLSearchParams();
      if (variantFilter) params.append('variant', variantFilter);
      if (qualityFilter) params.append('quality_change', qualityFilter);
      params.append('limit', '100');

      const resultsResponse = await adminAPI.fetch(`/api/replay-experiments/${experimentId}/results?${params}`);
      const resultsData = await resultsResponse.json();
      if (resultsData.success) {
        setResults(resultsData.results);
      }

      setError(null);
    } catch (e) {
      setError('Failed to load experiment data');
    } finally {
      setLoading(false);
    }
  }, [experimentId, variantFilter, qualityFilter]);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  // Polling for running experiments
  useEffect(() => {
    if (isPolling) {
      const interval = setInterval(fetchData, 5000);
      return () => clearInterval(interval);
    }
  }, [isPolling, fetchData]);

  const launchExperiment = async () => {
    try {
      const response = await adminAPI.fetch(`/api/replay-experiments/${experimentId}/launch`, {
        method: 'POST'
      });
      const data = await response.json();
      if (data.success) {
        setIsPolling(true);
        fetchData();
      } else {
        setError(data.error || 'Failed to launch experiment');
      }
    } catch (e) {
      setError('Failed to connect to server');
    }
  };

  if (loading) {
    return (
      <div className="rr-loading">
        <div className="rr-loading__spinner" />
        <span>Loading results...</span>
      </div>
    );
  }

  if (!experiment) {
    return (
      <div className="rr-error">
        <AlertCircle size={48} />
        <p>Experiment not found</p>
      </div>
    );
  }

  const progress = experiment.results_total > 0
    ? Math.round((experiment.results_completed / experiment.results_total) * 100)
    : 0;

  return (
    <div className="rr-container">
      {/* Header */}
      <div className="rr-header">
        <div className="rr-header__info">
          <h2>{experiment.name}</h2>
          {experiment.description && <p>{experiment.description}</p>}
        </div>
        <div className="rr-header__actions">
          {experiment.status === 'pending' && (
            <button className="rr-btn rr-btn--primary" onClick={launchExperiment}>
              <Zap size={16} />
              Launch Experiment
            </button>
          )}
          <button className="rr-btn rr-btn--secondary" onClick={fetchData}>
            <RefreshCw size={16} className={isPolling ? 'rr-spin' : ''} />
            Refresh
          </button>
        </div>
      </div>

      {/* Status Badge & Progress */}
      <div className="rr-status-bar">
        <span className={`rr-status-badge rr-status-badge--${experiment.status}`}>
          {experiment.status}
        </span>
        <div className="rr-progress">
          <div className="rr-progress__bar">
            <div
              className="rr-progress__fill"
              style={{ width: `${progress}%` }}
            />
          </div>
          <span className="rr-progress__text">
            {experiment.results_completed} / {experiment.results_total} ({progress}%)
          </span>
        </div>
      </div>

      {/* Summary Cards */}
      {summary && (
        <div className="rr-summary">
          <div className="rr-summary-card">
            <div className="rr-summary-card__icon">
              <BarChart3 size={24} />
            </div>
            <div className="rr-summary-card__content">
              <span className="rr-summary-card__value">{summary.overall.total}</span>
              <span className="rr-summary-card__label">Total Results</span>
            </div>
          </div>

          <div className="rr-summary-card rr-summary-card--success">
            <div className="rr-summary-card__icon">
              <TrendingUp size={24} />
            </div>
            <div className="rr-summary-card__content">
              <span className="rr-summary-card__value">{summary.overall.improved}</span>
              <span className="rr-summary-card__label">Improved</span>
            </div>
          </div>

          <div className="rr-summary-card rr-summary-card--danger">
            <div className="rr-summary-card__icon">
              <TrendingDown size={24} />
            </div>
            <div className="rr-summary-card__content">
              <span className="rr-summary-card__value">{summary.overall.degraded}</span>
              <span className="rr-summary-card__label">Degraded</span>
            </div>
          </div>

          <div className="rr-summary-card">
            <div className="rr-summary-card__icon">
              <Minus size={24} />
            </div>
            <div className="rr-summary-card__content">
              <span className="rr-summary-card__value">{summary.overall.actions_changed}</span>
              <span className="rr-summary-card__label">Actions Changed</span>
            </div>
          </div>
        </div>
      )}

      {/* Variant Breakdown */}
      {summary && Object.keys(summary.by_variant).length > 0 && (
        <div className="rr-variants">
          <div className="rr-variants__header">
            <h3>Results by Variant</h3>
            <button
              className="rr-variants__toggle"
              onClick={() => setShowDetails(!showDetails)}
            >
              {showDetails ? 'Hide Details' : 'Show Details'}
              {showDetails ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
            </button>
          </div>

          <div className="rr-variants__table">
            <table>
              <thead>
                <tr>
                  <th>Variant</th>
                  <th>Total</th>
                  <th>Improved</th>
                  <th>Degraded</th>
                  <th>Changed</th>
                  {showDetails && (
                    <>
                      <th>Avg Latency</th>
                      <th>Tokens</th>
                      <th>Errors</th>
                    </>
                  )}
                </tr>
              </thead>
              <tbody>
                {Object.entries(summary.by_variant).map(([variant, stats]) => (
                  <tr key={variant}>
                    <td className="rr-variant-name">{variant}</td>
                    <td>{stats.total}</td>
                    <td className="rr-cell--success">{stats.improved}</td>
                    <td className="rr-cell--danger">{stats.degraded}</td>
                    <td>{stats.actions_changed}</td>
                    {showDetails && (
                      <>
                        <td>{stats.avg_latency ? `${Math.round(stats.avg_latency)}ms` : '-'}</td>
                        <td>{(stats.total_input_tokens + stats.total_output_tokens).toLocaleString()}</td>
                        <td className={stats.errors > 0 ? 'rr-cell--danger' : ''}>{stats.errors}</td>
                      </>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Results Filter */}
      <div className="rr-filter">
        <div className="rr-filter__group">
          <label>Variant</label>
          <select
            value={variantFilter}
            onChange={(e) => setVariantFilter(e.target.value)}
          >
            <option value="">All variants</option>
            {summary && Object.keys(summary.by_variant).map(v => (
              <option key={v} value={v}>{v}</option>
            ))}
          </select>
        </div>
        <div className="rr-filter__group">
          <label>Quality Change</label>
          <select
            value={qualityFilter}
            onChange={(e) => setQualityFilter(e.target.value)}
          >
            <option value="">All</option>
            <option value="improved">Improved</option>
            <option value="degraded">Degraded</option>
            <option value="unchanged">Unchanged</option>
          </select>
        </div>
      </div>

      {/* Results List */}
      <div className="rr-results">
        <h3>Individual Results</h3>
        {results.length === 0 ? (
          <div className="rr-results__empty">
            {experiment.status === 'pending'
              ? 'Launch the experiment to see results'
              : 'No results yet'}
          </div>
        ) : (
          <div className="rr-results__list">
            {results.map((result) => (
              <div
                key={result.id}
                className={`rr-result-card ${result.quality_change ? `rr-result-card--${result.quality_change}` : ''}`}
              >
                <div className="rr-result-card__header">
                  <span className="rr-result-card__player">{result.player_name}</span>
                  <span className="rr-result-card__variant">{result.variant}</span>
                </div>
                <div className="rr-result-card__body">
                  <div className="rr-result-card__action">
                    <span className={`rr-action rr-action--${result.original_action}`}>
                      {result.original_action}
                    </span>
                    <ArrowRight size={16} className={result.action_changed ? 'rr-changed' : ''} />
                    <span className={`rr-action rr-action--${result.new_action}`}>
                      {result.new_action}
                    </span>
                  </div>
                  <div className="rr-result-card__meta">
                    <span className="rr-result-card__phase">{result.phase}</span>
                    {result.pot_odds && (
                      <span className="rr-result-card__odds">{result.pot_odds.toFixed(2)} odds</span>
                    )}
                    <span className="rr-result-card__latency">
                      <Clock size={12} />
                      {result.latency_ms}ms
                    </span>
                  </div>
                </div>
                {result.quality_change && (
                  <div className={`rr-result-card__badge rr-result-card__badge--${result.quality_change}`}>
                    {result.quality_change === 'improved' && <TrendingUp size={14} />}
                    {result.quality_change === 'degraded' && <TrendingDown size={14} />}
                    {result.quality_change === 'unchanged' && <Minus size={14} />}
                    {result.quality_change}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

export default ReplayResults;
