/**
 * EnrichmentPanel - Collapsible side panel for action enrichment data
 *
 * Shows equity bar, decision quality badge, and AI thinking text.
 * Only renders content when enrichment data is available.
 */

import { memo, useState, useCallback } from 'react';
import { ChevronDown, ChevronUp, Brain } from 'lucide-react';
import type { EnrichmentData } from './types';

interface EnrichmentPanelProps {
  enrichment: EnrichmentData | null;
  playerName: string | null;
}

const QUALITY_COLORS: Record<string, string> = {
  optimal: 'var(--color-emerald)',
  good: 'var(--color-sapphire)',
  neutral: 'var(--color-text-secondary)',
  suboptimal: 'var(--color-amber)',
  bad: 'var(--color-ruby)',
};

export const EnrichmentPanel = memo(function EnrichmentPanel({
  enrichment,
  playerName,
}: EnrichmentPanelProps) {
  const [isExpanded, setIsExpanded] = useState(true);

  const toggle = useCallback(() => setIsExpanded((prev) => !prev), []);

  if (!enrichment) {
    return (
      <div className="enrichment-panel enrichment-panel--empty">
        <div className="enrichment-panel__header" onClick={toggle}>
          <Brain size={14} />
          <span>Enrichment</span>
        </div>
        <div className="enrichment-panel__no-data">No enrichment data for this action</div>
      </div>
    );
  }

  const qualityColor = enrichment.decision_quality
    ? QUALITY_COLORS[enrichment.decision_quality] ?? 'var(--color-text-secondary)'
    : undefined;

  return (
    <div className="enrichment-panel">
      <button className="enrichment-panel__header" onClick={toggle} type="button">
        <Brain size={14} />
        <span>Enrichment{playerName ? ` — ${playerName}` : ''}</span>
        {isExpanded ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
      </button>

      {isExpanded && (
        <div className="enrichment-panel__body">
          {/* Equity bar */}
          {enrichment.equity != null && (
            <div className="enrichment-panel__section">
              <span className="enrichment-panel__label">Equity</span>
              <div className="enrichment-panel__equity-bar">
                <div
                  className="enrichment-panel__equity-fill"
                  style={{ width: `${Math.round(enrichment.equity * 100)}%` }}
                />
              </div>
              <span className="enrichment-panel__equity-value">
                {Math.round(enrichment.equity * 100)}%
              </span>
            </div>
          )}

          {/* Decision quality */}
          {enrichment.decision_quality && (
            <div className="enrichment-panel__section">
              <span className="enrichment-panel__label">Decision Quality</span>
              <span
                className="enrichment-panel__quality-badge"
                style={{ color: qualityColor }}
              >
                {enrichment.decision_quality.toUpperCase()}
              </span>
            </div>
          )}

          {/* AI thinking */}
          {enrichment.ai_thinking && (
            <div className="enrichment-panel__section">
              <span className="enrichment-panel__label">AI Thinking</span>
              <p className="enrichment-panel__thinking">{enrichment.ai_thinking}</p>
            </div>
          )}
        </div>
      )}
    </div>
  );
});
