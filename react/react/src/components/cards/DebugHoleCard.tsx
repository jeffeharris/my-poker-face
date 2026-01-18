import { useState, useCallback } from 'react';
import type { LLMDebugInfo } from '../../types/player';
import { formatCost, formatLatency, truncate } from '../../utils/formatters';
import './DebugHoleCard.css';

interface DebugHoleCardProps {
  debugInfo?: LLMDebugInfo;
  className?: string;
}

/**
 * A flippable hole card that reveals LLM debug info when clicked.
 * Features a CRT phosphor terminal aesthetic on the debug side.
 */
export function DebugHoleCard({ debugInfo, className = '' }: DebugHoleCardProps) {
  const [isFlipped, setIsFlipped] = useState(false);

  const handleClick = useCallback(() => {
    if (debugInfo) {
      setIsFlipped(prev => !prev);
    }
  }, [debugInfo]);

  const handleBackdropClick = useCallback((e: React.MouseEvent) => {
    e.stopPropagation();
    setIsFlipped(false);
  }, []);

  return (
    <>
      {/* Mobile backdrop */}
      {isFlipped && (
        <div
          className="debug-backdrop"
          onClick={handleBackdropClick}
          aria-hidden="true"
        />
      )}

      <div
        className={`debug-hole-card ${isFlipped ? 'active' : ''} ${debugInfo ? 'has-debug' : ''} ${className}`}
        onClick={handleClick}
        role="button"
        tabIndex={debugInfo ? 0 : -1}
        aria-label={debugInfo ? 'Click to view AI model info' : 'AI player card'}
        onKeyDown={(e) => e.key === 'Enter' && handleClick()}
      >
        <div className={`debug-card-inner ${isFlipped ? 'flipped' : ''}`}>
          {/* Front: Normal card back */}
          <div className="debug-card-front">
            <div className="card-back-pattern"></div>
          </div>

          {/* Back: Debug panel */}
          <div className="debug-card-back">
            <div className="crt-screen">
              {/* Scanline overlay */}
              <div className="scanlines" />

              {/* Header */}
              <div className="debug-header">
                <span className="block-char"></span>
                <span className="header-text">SYS.DEBUG</span>
                <span className="block-char"></span>
              </div>

              {/* Stats section */}
              {debugInfo ? (
                <div className="debug-stats">
                  <div className="stat-row" style={{ animationDelay: '0ms' }}>
                    <span className="stat-label">VNDR</span>
                    <span className="stat-value">{debugInfo.provider}</span>
                  </div>
                  <div className="stat-row" style={{ animationDelay: '50ms' }}>
                    <span className="stat-label">MDL</span>
                    <span className="stat-value" title={debugInfo.model}>
                      {truncate(debugInfo.model, 14)}
                    </span>
                  </div>
                  {debugInfo.reasoning_effort && (
                    <div className="stat-row" style={{ animationDelay: '100ms' }}>
                      <span className="stat-label">RSN</span>
                      <span className="stat-value">{debugInfo.reasoning_effort}</span>
                    </div>
                  )}

                  <div className="stat-divider" />

                  <div className="stat-row" style={{ animationDelay: '150ms' }}>
                    <span className="stat-label">LAT</span>
                    <span className="stat-value stat-latency">
                      {formatLatency(debugInfo.avg_latency_ms)}
                    </span>
                  </div>
                  <div className="stat-row" style={{ animationDelay: '200ms' }}>
                    <span className="stat-label">COST</span>
                    <span className={`stat-value stat-cost ${debugInfo.avg_cost_per_call > 0.01 ? 'high-cost' : ''}`}>
                      {formatCost(debugInfo.avg_cost_per_call)}
                    </span>
                  </div>

                  <div className="stat-divider" />

                  {/* Call count with mini progress bar */}
                  <div className="stat-footer" style={{ animationDelay: '250ms' }}>
                    <div className="progress-bar">
                      {Array.from({ length: Math.min(debugInfo.total_calls, 12) }).map((_, i) => (
                        <span key={i} className="progress-block"></span>
                      ))}
                    </div>
                    <span className="call-count">{debugInfo.total_calls} calls</span>
                  </div>
                </div>
              ) : (
                <div className="debug-stats no-data">
                  <span className="no-data-text">NO DATA</span>
                </div>
              )}
            </div>
          </div>
        </div>
      </div>
    </>
  );
}
