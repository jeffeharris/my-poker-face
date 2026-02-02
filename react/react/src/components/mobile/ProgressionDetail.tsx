import type { ProgressionState, CoachProgression, SkillProgress, FullSkillProgress, SkillStateValue } from '../../types/coach';
import './ProgressionDetail.css';

interface ProgressionDetailProps {
  progressionFull: ProgressionState | null;
  progressionLite: CoachProgression | null;
}

// All 4 gates in order with their skill IDs
const GATE_CONFIG: { gate: number; name: string; skillIds: string[] }[] = [
  {
    gate: 1,
    name: 'Preflop Fundamentals',
    skillIds: ['fold_trash_hands', 'position_matters', 'raise_or_fold'],
  },
  {
    gate: 2,
    name: 'Post-Flop Basics',
    skillIds: ['flop_connection', 'bet_when_strong', 'checking_is_allowed'],
  },
  {
    gate: 3,
    name: 'Pressure Recognition',
    skillIds: ['draws_need_price', 'respect_big_bets', 'have_a_plan'],
  },
  {
    gate: 4,
    name: 'Multi-Street Thinking',
    skillIds: ['dont_pay_double_barrels', 'size_bets_with_purpose'],
  },
];

function stateColorClass(state: SkillStateValue): string {
  switch (state) {
    case 'introduced': return 'state-introduced';
    case 'practicing': return 'state-practicing';
    case 'reliable': return 'state-reliable';
    case 'automatic': return 'state-automatic';
    default: return 'state-introduced';
  }
}

function stateLabel(state: SkillStateValue): string {
  return state.charAt(0).toUpperCase() + state.slice(1);
}

export function ProgressionDetail({ progressionFull, progressionLite }: ProgressionDetailProps) {
  // Prefer full data, fall back to lite skill_states
  const skillStates: Record<string, SkillProgress | FullSkillProgress> =
    progressionFull?.skill_states ?? progressionLite?.skill_states ?? {};
  const gateProgress = progressionFull?.gate_progress ?? {};

  return (
    <div className="progression-detail">
      {GATE_CONFIG.map(({ gate, name, skillIds }) => {
        const gateInfo = gateProgress[String(gate)];
        const isLocked = gateInfo ? !gateInfo.unlocked : gate > 1;

        // Summary dots for gate header
        const gateSkills = skillIds
          .map(sid => skillStates[sid])
          .filter(Boolean);

        return (
          <div
            key={gate}
            className={`progression-detail__gate ${isLocked ? 'progression-detail__gate--locked' : ''}`}
          >
            <div className="progression-detail__gate-header">
              <span className="progression-detail__gate-label">Gate {gate}</span>
              <span className="progression-detail__gate-name">{name}</span>
              <div className="progression-detail__gate-dots">
                {skillIds.map(sid => {
                  const skill = skillStates[sid];
                  const dotClass = skill ? stateColorClass(skill.state) : 'state-locked';
                  return <span key={sid} className={`progression-detail__dot ${dotClass}`} />;
                })}
              </div>
            </div>

            <div className="progression-detail__skills">
              {skillIds.map(sid => {
                const skill = skillStates[sid];
                if (!skill && !isLocked) return null;

                const skillName = skill?.name ?? sid.replace(/_/g, ' ');
                const accuracy = skill?.window_accuracy ?? 0;
                const state = skill?.state ?? 'introduced';

                return (
                  <div
                    key={sid}
                    className={`progression-detail__skill-card ${isLocked && !skill ? 'progression-detail__skill-card--locked' : ''}`}
                  >
                    <div className="progression-detail__skill-top">
                      <span className="progression-detail__skill-name">{skillName}</span>
                      {skill && (
                        <span className={`progression-detail__skill-accuracy ${stateColorClass(state)}`}>
                          {Math.round(accuracy * 100)}%
                        </span>
                      )}
                    </div>
                    <div className="progression-detail__skill-bar">
                      <div
                        className={`progression-detail__skill-bar-fill ${stateColorClass(state)}`}
                        style={{ width: skill ? `${Math.round(accuracy * 100)}%` : '0%' }}
                      />
                    </div>
                    <span className={`progression-detail__skill-state ${stateColorClass(state)}`}>
                      {skill ? stateLabel(state) : 'Locked'}
                    </span>
                  </div>
                );
              })}
            </div>
          </div>
        );
      })}
    </div>
  );
}
