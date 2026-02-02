import { useMemo } from 'react';
import type { ProgressionState, CoachProgression, SkillProgress, FullSkillProgress, SkillStateValue } from '../../types/coach';
import './ProgressionDetail.css';

interface ProgressionDetailProps {
  progressionFull: ProgressionState | null;
  progressionLite: CoachProgression | null;
}

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

  // Derive gate structure from API data (single source of truth)
  const gates = useMemo(() => {
    const gateMap = new Map<number, { gate: number; name: string; skillIds: string[] }>();

    // Seed gates from gate_progress (includes name metadata)
    for (const [gateNum, info] of Object.entries(gateProgress)) {
      const g = Number(gateNum);
      gateMap.set(g, { gate: g, name: info.name, skillIds: [] });
    }

    // Group skills by their gate field
    for (const [sid, skill] of Object.entries(skillStates)) {
      const g = skill.gate;
      if (!gateMap.has(g)) {
        gateMap.set(g, { gate: g, name: `Gate ${g}`, skillIds: [] });
      }
      gateMap.get(g)!.skillIds.push(sid);
    }

    // Sort gates by number, skills alphabetically within each gate
    return Array.from(gateMap.values())
      .sort((a, b) => a.gate - b.gate)
      .map(g => ({ ...g, skillIds: g.skillIds.sort() }));
  }, [gateProgress, skillStates]);

  if (gates.length === 0) return null;

  return (
    <div className="progression-detail">
      {gates.map(({ gate, name, skillIds }) => {
        const gateInfo = gateProgress[String(gate)];
        const isLocked = gateInfo ? !gateInfo.unlocked : gate > 1;

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
