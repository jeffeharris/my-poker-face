import { useEffect, useState } from 'react';
import { Sprout, Target, Flame, ChevronRight, GraduationCap } from 'lucide-react';
import { PageLayout, PageHeader, MenuBar, BackButton } from '../shared';
import { config } from '../../config';
import { logger } from '../../utils/logger';
import './TrainingMenu.css';

export type TrainingDifficulty = 'easy' | 'medium' | 'hard';

interface DifficultyOption {
  id: TrainingDifficulty;
  title: string;
  blurb: string;
  icon: typeof Sprout;
  variant: string;
}

// The opponent styles here mirror training/opponent_roster.py so the menu
// copy stays honest about who you'll actually face.
const DIFFICULTIES: DifficultyOption[] = [
  {
    id: 'easy',
    title: 'Easy',
    blurb: 'Loose, passive opponents — calling stations and over-folders. Practice value-betting and punishing leaks.',
    icon: Sprout,
    variant: 'training-card--easy',
  },
  {
    id: 'medium',
    title: 'Medium',
    blurb: 'Solid, predictable bots that play by pot odds and a sound baseline. Good for honing fundamentals.',
    icon: Target,
    variant: 'training-card--medium',
  },
  {
    id: 'hard',
    title: 'Hard',
    blurb: 'The sharp solver — tough, balanced play. Test your reads and your discipline.',
    icon: Flame,
    variant: 'training-card--hard',
  },
];

interface TablePreset {
  id: string;
  title: string;
  description: string;
  opponents: number;
  big_blind: number;
  starting_stack_bb: number;
}

// Fallback if the presets endpoint is unreachable — the backend default is
// 'standard', which is always a safe choice to send.
const FALLBACK_PRESETS: TablePreset[] = [
  { id: 'standard', title: '6-Max', description: 'Five opponents, 100bb deep.', opponents: 5, big_blind: 100, starting_stack_bb: 100 },
];

interface TrainingMenuProps {
  playerName: string;
  onStart: (difficulty: TrainingDifficulty, presetId: string) => void;
  onBack: () => void;
  isCreating?: boolean;
}

export function TrainingMenu({ playerName, onStart, onBack, isCreating = false }: TrainingMenuProps) {
  const [presets, setPresets] = useState<TablePreset[]>(FALLBACK_PRESETS);
  const [presetId, setPresetId] = useState('standard');

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const resp = await fetch(`${config.API_URL}/api/training/scenarios`, {
          credentials: 'include',
        });
        if (!resp.ok) return;
        const data = await resp.json();
        if (cancelled || !Array.isArray(data.presets) || data.presets.length === 0) return;
        setPresets(data.presets);
        setPresetId(data.default_preset_id ?? data.presets[0].id);
      } catch (err) {
        logger.error('Failed to load training presets:', err);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <>
      <MenuBar showUserInfo />
      <PageLayout variant="top" glowColor="emerald" maxWidth="md" hasMenuBar>
        <BackButton onClick={onBack} />

        <div className="training-menu__crest">
          <GraduationCap size={30} strokeWidth={1.5} />
        </div>
        <PageHeader
          title="Practice"
          subtitle="Sparring with a coach — these games don't count"
          titleVariant="primary"
        />

        <div className="training-menu__intro">
          Pick a table and how tough you want it, {playerName}. Your coach is on
          the whole time, and nothing here touches your bankroll, reputation, or
          stats.
        </div>

        <fieldset className="training-menu__sizes" disabled={isCreating}>
          <legend className="training-menu__sizes-label">Table</legend>
          <div className="training-menu__size-row" role="radiogroup" aria-label="Table">
            {presets.map((p) => (
              <button
                key={p.id}
                type="button"
                role="radio"
                aria-checked={presetId === p.id}
                title={p.description}
                className={
                  'training-menu__size' +
                  (presetId === p.id ? ' training-menu__size--active' : '')
                }
                onClick={() => setPresetId(p.id)}
              >
                {p.title}
              </button>
            ))}
          </div>
          <p className="training-menu__size-hint">
            {presets.find((p) => p.id === presetId)?.description ?? ''}
          </p>
        </fieldset>

        <div className="training-menu__difficulties">
          {DIFFICULTIES.map((d) => {
            const Icon = d.icon;
            return (
              <button
                key={d.id}
                type="button"
                className={`training-card ${d.variant}`}
                onClick={() => onStart(d.id, presetId)}
                disabled={isCreating}
              >
                <div className="training-card__icon-wrap">
                  <Icon className="training-card__icon" size={48} strokeWidth={1.5} />
                </div>
                <div className="training-card__content">
                  <h2 className="training-card__title">{d.title}</h2>
                  <p className="training-card__blurb">{d.blurb}</p>
                </div>
                <ChevronRight className="training-card__arrow" size={22} />
              </button>
            );
          })}
        </div>
      </PageLayout>
    </>
  );
}
