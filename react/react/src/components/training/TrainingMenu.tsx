import { useEffect, useState } from 'react';
import { Sprout, Target, Flame, GraduationCap, Play } from 'lucide-react';
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
}

// Mirrors training/opponent_roster.py so the copy is honest about who you face.
const DIFFICULTIES: DifficultyOption[] = [
  {
    id: 'easy',
    title: 'Easy',
    blurb: 'Loose, passive opponents — stations and over-folders. Practice value-betting and punishing leaks.',
    icon: Sprout,
  },
  {
    id: 'medium',
    title: 'Medium',
    blurb: 'Solid, predictable bots that play by pot odds and a sound baseline. Hone your fundamentals.',
    icon: Target,
  },
  {
    id: 'hard',
    title: 'Hard',
    blurb: 'The sharp solver — tough, balanced play. Test your reads and your discipline.',
    icon: Flame,
  },
];

interface TablePreset {
  id: string;
  title: string;
  description: string;
}

const FALLBACK_PRESETS: TablePreset[] = [
  { id: 'standard', title: '6-Max', description: 'Five opponents, 100bb deep.' },
];

interface TrainingMenuProps {
  playerName: string;
  onStart: (difficulty: TrainingDifficulty, presetId: string) => void;
  onBack: () => void;
  isCreating?: boolean;
}

export function TrainingMenu({ playerName, onStart, onBack, isCreating = false }: TrainingMenuProps) {
  const [difficulty, setDifficulty] = useState<TrainingDifficulty>('medium');
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
        if (cancelled) return;
        if (Array.isArray(data.presets) && data.presets.length > 0) {
          setPresets(data.presets);
          setPresetId(data.default_preset_id ?? data.presets[0].id);
        }
      } catch (err) {
        logger.error('Failed to load training scenarios:', err);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const activeDifficulty = DIFFICULTIES.find((d) => d.id === difficulty)!;

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
          Pick how tough the table is, {playerName} — it applies to free play and
          drills alike. The coach is on the whole time; nothing here touches your
          bankroll, reputation, or stats.
        </div>

        {/* Difficulty — shared by free play and drills */}
        <fieldset className="training-menu__group" disabled={isCreating}>
          <legend className="training-menu__group-label">Difficulty</legend>
          <div className="training-menu__chips" role="radiogroup" aria-label="Difficulty">
            {DIFFICULTIES.map((d) => {
              const Icon = d.icon;
              const active = difficulty === d.id;
              return (
                <button
                  key={d.id}
                  type="button"
                  role="radio"
                  aria-checked={active}
                  className={
                    `training-menu__chip training-menu__chip--${d.id}` +
                    (active ? ' training-menu__chip--active' : '')
                  }
                  onClick={() => setDifficulty(d.id)}
                >
                  <Icon size={20} strokeWidth={1.75} />
                  {d.title}
                </button>
              );
            })}
          </div>
          <p className="training-menu__hint">{activeDifficulty.blurb}</p>
        </fieldset>

        {/* Free play — pick a table shape and deal in */}
        <fieldset className="training-menu__group" disabled={isCreating}>
          <legend className="training-menu__group-label">Free play</legend>
          <div className="training-menu__chips" role="radiogroup" aria-label="Table">
            {presets.map((p) => (
              <button
                key={p.id}
                type="button"
                role="radio"
                aria-checked={presetId === p.id}
                title={p.description}
                className={
                  'training-menu__chip' + (presetId === p.id ? ' training-menu__chip--active' : '')
                }
                onClick={() => setPresetId(p.id)}
              >
                {p.title}
              </button>
            ))}
          </div>
          <p className="training-menu__hint">
            {presets.find((p) => p.id === presetId)?.description ?? ''}
          </p>
          <button
            type="button"
            className="training-menu__primary"
            disabled={isCreating}
            onClick={() => onStart(difficulty, presetId)}
          >
            <Play size={18} /> Deal me in
          </button>
        </fieldset>
      </PageLayout>
    </>
  );
}
