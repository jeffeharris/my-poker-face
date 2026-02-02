import { ChevronDown, ChevronUp } from 'lucide-react';
import type { CoachProgression } from '../../types/coach';
import './ProgressionStrip.css';

interface ProgressionStripProps {
  progression: CoachProgression;
  isExpanded: boolean;
  onToggle: () => void;
}

function accuracyColor(accuracy: number): string {
  if (accuracy >= 0.7) return 'emerald';
  if (accuracy >= 0.4) return 'gold';
  return 'ruby';
}

export function ProgressionStrip({ progression, isExpanded, onToggle }: ProgressionStripProps) {
  const primarySkillId = progression.primary_skill;
  const primarySkill = primarySkillId ? progression.skill_states[primarySkillId] : null;

  const gateNumber = primarySkill?.gate ?? 1;
  const accuracy = primarySkill?.window_accuracy ?? 0;
  const colorClass = accuracyColor(accuracy);

  return (
    <button className="progression-strip" onClick={onToggle} aria-label="Toggle skill detail" aria-expanded={isExpanded}>
      <div className="progression-strip__top">
        <span className="progression-strip__gate">Gate {gateNumber}</span>
        <span className="progression-strip__skill">
          {primarySkill ? primarySkill.name : 'All skills mastered'}
        </span>
        {primarySkill && (
          <span className={`progression-strip__accuracy progression-strip__accuracy--${colorClass}`}>
            {Math.round(accuracy * 100)}%
          </span>
        )}
        <span className="progression-strip__chevron">
          {isExpanded ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
        </span>
      </div>
      {primarySkill && (
        <div className={`progression-strip__bar progression-strip__bar--${colorClass}`}>
          <div
            className="progression-strip__bar-fill"
            style={{ width: `${Math.round(accuracy * 100)}%` }}
          />
        </div>
      )}
    </button>
  );
}
