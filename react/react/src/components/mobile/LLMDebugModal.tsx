import { useEffect, useRef } from 'react';
import { X } from 'lucide-react';
import type { LLMDebugInfo } from '../../types/player';
import './LLMDebugModal.css';

interface LLMDebugModalProps {
  isOpen: boolean;
  onClose: () => void;
  playerName: string;
  debugInfo?: LLMDebugInfo;
}

/**
 * Mobile bottom sheet modal showing AI player's LLM debug info.
 * Features CRT phosphor terminal aesthetic.
 */
export function LLMDebugModal({ isOpen, onClose, playerName, debugInfo }: LLMDebugModalProps) {
  const modalRef = useRef<HTMLDivElement>(null);

  // Close on escape key
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    if (isOpen) {
      document.addEventListener('keydown', handleKeyDown);
      return () => document.removeEventListener('keydown', handleKeyDown);
    }
  }, [isOpen, onClose]);

  // Prevent body scroll when modal is open
  useEffect(() => {
    if (isOpen) {
      document.body.style.overflow = 'hidden';
      return () => { document.body.style.overflow = ''; };
    }
  }, [isOpen]);

  if (!isOpen) return null;

  // Format cost with appropriate precision
  const formatCost = (cost: number) => {
    if (cost === 0) return '$0.00';
    if (cost < 0.0001) return `$${cost.toExponential(1)}`;
    if (cost < 0.01) return `$${cost.toFixed(4)}`;
    return `$${cost.toFixed(3)}`;
  };

  // Format latency
  const formatLatency = (ms: number) => {
    if (ms >= 1000) return `${(ms / 1000).toFixed(1)}s`;
    return `${Math.round(ms)}ms`;
  };

  return (
    <div className="llm-debug-modal-overlay" onClick={onClose}>
      <div
        ref={modalRef}
        className="llm-debug-modal"
        onClick={e => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-labelledby="llm-debug-title"
      >
        {/* CRT screen effect */}
        <div className="llm-debug-crt">
          {/* Scanlines */}
          <div className="llm-debug-scanlines" />

          {/* Header */}
          <div className="llm-debug-header">
            <div className="llm-debug-title-row">
              <span className="block-char"></span>
              <h3 id="llm-debug-title" className="llm-debug-title">SYS.DEBUG</h3>
              <span className="block-char"></span>
            </div>
            <button className="llm-debug-close" onClick={onClose} aria-label="Close">
              <X size={20} />
            </button>
          </div>

          {/* Player name */}
          <div className="llm-debug-player">
            <span className="player-label">PLAYER:</span>
            <span className="player-value">{playerName}</span>
          </div>

          {/* Stats */}
          {debugInfo ? (
            <div className="llm-debug-stats">
              <div className="stat-group">
                <div className="stat-row">
                  <span className="stat-label">VENDOR</span>
                  <span className="stat-value">{debugInfo.provider}</span>
                </div>
                <div className="stat-row">
                  <span className="stat-label">MODEL</span>
                  <span className="stat-value model-value">{debugInfo.model}</span>
                </div>
                {debugInfo.reasoning_effort && (
                  <div className="stat-row">
                    <span className="stat-label">REASONING</span>
                    <span className="stat-value">{debugInfo.reasoning_effort}</span>
                  </div>
                )}
              </div>

              <div className="stat-divider" />

              <div className="stat-group">
                <div className="stat-row">
                  <span className="stat-label">AVG LATENCY</span>
                  <span className="stat-value latency-value">
                    {formatLatency(debugInfo.avg_latency_ms)}
                  </span>
                </div>
                <div className="stat-row">
                  <span className="stat-label">AVG COST</span>
                  <span className={`stat-value cost-value ${debugInfo.avg_cost_per_call > 0.01 ? 'high-cost' : ''}`}>
                    {formatCost(debugInfo.avg_cost_per_call)}
                  </span>
                </div>
              </div>

              <div className="stat-divider" />

              {/* Call count with progress visualization */}
              <div className="stat-footer">
                <div className="progress-visualization">
                  {Array.from({ length: Math.min(debugInfo.total_calls, 20) }).map((_, i) => (
                    <span key={i} className="progress-block"></span>
                  ))}
                </div>
                <span className="call-count">{debugInfo.total_calls} API calls this game</span>
              </div>
            </div>
          ) : (
            <div className="llm-debug-no-data">
              <span className="no-data-text">NO DATA AVAILABLE</span>
              <span className="no-data-hint">Play some hands to see stats</span>
            </div>
          )}

          {/* Footer */}
          <div className="llm-debug-footer">
            <span className="footer-text">TAP OUTSIDE TO CLOSE</span>
          </div>
        </div>
      </div>
    </div>
  );
}
