import { useState } from 'react';
import { Sprout, Target, Flame, ChevronRight, GraduationCap } from 'lucide-react';
import { PageLayout, PageHeader, MenuBar, BackButton } from '../shared';
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

// Table-size presets (opponent_count). Heads-up is a single opponent; full
// ring tops out at the 8-opponent backend cap. Table *presets* (short/deep
// stacks) arrive in a later phase — this is just seat count for now.
const TABLE_SIZES: { id: string; label: string; opponents: number }[] = [
  { id: 'hu', label: 'Heads-up', opponents: 1 },
  { id: 'short', label: '3-handed', opponents: 2 },
  { id: 'full', label: '6-max', opponents: 5 },
];

interface TrainingMenuProps {
  playerName: string;
  onStart: (difficulty: TrainingDifficulty, opponentCount: number) => void;
  onBack: () => void;
  isCreating?: boolean;
}

export function TrainingMenu({ playerName, onStart, onBack, isCreating = false }: TrainingMenuProps) {
  const [tableSizeId, setTableSizeId] = useState('full');
  const opponentCount =
    TABLE_SIZES.find((t) => t.id === tableSizeId)?.opponents ?? 5;

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
          Pick how tough the table should be, {playerName}. Your coach is on the
          whole time, and nothing here touches your bankroll, reputation, or
          stats.
        </div>

        <fieldset className="training-menu__sizes" disabled={isCreating}>
          <legend className="training-menu__sizes-label">Table size</legend>
          <div className="training-menu__size-row" role="radiogroup" aria-label="Table size">
            {TABLE_SIZES.map((t) => (
              <button
                key={t.id}
                type="button"
                role="radio"
                aria-checked={tableSizeId === t.id}
                className={
                  'training-menu__size' +
                  (tableSizeId === t.id ? ' training-menu__size--active' : '')
                }
                onClick={() => setTableSizeId(t.id)}
              >
                {t.label}
              </button>
            ))}
          </div>
        </fieldset>

        <div className="training-menu__difficulties">
          {DIFFICULTIES.map((d) => {
            const Icon = d.icon;
            return (
              <button
                key={d.id}
                type="button"
                className={`training-card ${d.variant}`}
                onClick={() => onStart(d.id, opponentCount)}
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
