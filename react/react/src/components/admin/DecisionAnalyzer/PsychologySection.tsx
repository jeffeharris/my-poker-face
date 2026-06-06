import type { DecisionAnalysis } from './types';
import { getTiltBarClass } from './utils';

interface PsychologySectionProps {
  analysis: DecisionAnalysis;
}

// Render psychology section (shared between mobile and desktop)
export function PsychologySection({ analysis }: PsychologySectionProps) {
  const hasPsychology =
    analysis.display_emotion != null ||
    analysis.tilt_level != null ||
    analysis.valence != null ||
    analysis.arousal != null ||
    analysis.control != null ||
    analysis.focus != null ||
    analysis.elastic_aggression != null ||
    analysis.elastic_bluff_tendency != null;

  if (!hasPsychology) return null;

  return (
    <div className="psychology-section">
      <h4>Psychology</h4>
      <div className="psychology-grid">
        {analysis.display_emotion != null && (
          <div className="psychology-item">
            <label>Emotion:</label>
            <span className={`emotion-badge emotion-badge--${analysis.display_emotion}`}>
              {analysis.display_emotion}
            </span>
          </div>
        )}
        {analysis.tilt_level != null && (
          <div className="psychology-item">
            <label>Tilt:</label>
            <div className="tilt-bar">
              <span>{(analysis.tilt_level * 100).toFixed(0)}%</span>
              <div className="tilt-bar-track">
                <div
                  className={`tilt-bar-fill ${getTiltBarClass(analysis.tilt_level)}`}
                  style={{ width: `${analysis.tilt_level * 100}%` }}
                />
              </div>
            </div>
            {analysis.tilt_source && (
              <span style={{ fontSize: '0.7rem', color: 'rgba(255,255,255,0.5)' }}>
                {analysis.tilt_source}
              </span>
            )}
          </div>
        )}
        {analysis.valence != null && (
          <div className="psychology-item">
            <label>Valence:</label>
            <span>{analysis.valence.toFixed(2)}</span>
          </div>
        )}
        {analysis.arousal != null && (
          <div className="psychology-item">
            <label>Arousal:</label>
            <span>{analysis.arousal.toFixed(2)}</span>
          </div>
        )}
        {analysis.control != null && (
          <div className="psychology-item">
            <label>Control:</label>
            <span>{analysis.control.toFixed(2)}</span>
          </div>
        )}
        {analysis.focus != null && (
          <div className="psychology-item">
            <label>Focus:</label>
            <span>{analysis.focus.toFixed(2)}</span>
          </div>
        )}
        {analysis.elastic_aggression != null && (
          <div className="psychology-item">
            <label>Aggression:</label>
            <span>{analysis.elastic_aggression.toFixed(2)}</span>
          </div>
        )}
        {analysis.elastic_bluff_tendency != null && (
          <div className="psychology-item">
            <label>Bluff Tendency:</label>
            <span>{analysis.elastic_bluff_tendency.toFixed(2)}</span>
          </div>
        )}
      </div>
    </div>
  );
}
