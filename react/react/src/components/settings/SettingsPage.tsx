import { useState } from 'react';
import { UserCircle, Gamepad2, ChevronRight, type LucideIcon } from 'lucide-react';
import { PageLayout, PageHeader, MenuBar } from '../shared';
import { ProfileSettings } from './ProfileSettings';
import { GameplaySettings } from './GameplaySettings';
import '../menus/HomeMenu.css'; // reuse the .mode-card menu system
import './SettingsPage.css';

type SectionId = 'profile' | 'gameplay';

interface Section {
  id: SectionId;
  label: string;
  /** One-liner shown on the menu card. */
  blurb: string;
  /** Sub-header shown once you're inside the section. */
  subtitle: string;
  icon: LucideIcon;
}

const SECTIONS: Section[] = [
  {
    id: 'profile',
    label: 'Profile',
    blurb: 'Your avatar and how you introduce yourself to the table',
    subtitle: 'Your avatar and how you introduce yourself to the table',
    icon: UserCircle,
  },
  {
    id: 'gameplay',
    label: 'Gameplay',
    blurb: 'How hands play out for you',
    subtitle: 'How hands play out for you',
    icon: Gamepad2,
  },
];

/**
 * SettingsPage — the user's home for personal preferences.
 *
 * A drill-in menu built on the app's existing `.mode-card` pattern (same as
 * HomeMenu): the top level is a list of section cards, tapping one opens that
 * section. This makes the sections unmistakable and scales cleanly as Coach /
 * World pace get added. Profile moved here from the former standalone /profile
 * page (which now redirects).
 */
export function SettingsPage({ onBack }: { onBack: () => void }) {
  const [active, setActive] = useState<SectionId | null>(null);
  const section = SECTIONS.find((s) => s.id === active) ?? null;

  return (
    <>
      <MenuBar
        onBack={section ? () => setActive(null) : onBack}
        title={section ? section.label : 'Settings'}
        showUserInfo
        onMainMenu={onBack}
      />
      <PageLayout variant="top" glowColor="sapphire" maxWidth="md" hasMenuBar>
        {!section ? (
          <>
            <PageHeader title="Settings" subtitle="Tune your profile and how the game plays" />
            <div className="settings-menu">
              {SECTIONS.map((s) => {
                const Icon = s.icon;
                return (
                  <button key={s.id} className="mode-card" onClick={() => setActive(s.id)}>
                    <div className="mode-card__icon-wrap">
                      <Icon className="mode-card__icon" size={64} strokeWidth={1.5} />
                    </div>
                    <div className="mode-card__content">
                      <h2 className="mode-card__title">{s.label}</h2>
                      <p className="mode-card__description">{s.blurb}</p>
                    </div>
                    <ChevronRight className="mode-card__arrow" size={22} />
                  </button>
                );
              })}
            </div>
          </>
        ) : (
          <>
            <PageHeader title={section.label} subtitle={section.subtitle} />
            <div className="settings-content">
              {active === 'profile' && <ProfileSettings />}
              {active === 'gameplay' && <GameplaySettings />}
            </div>
          </>
        )}
      </PageLayout>
    </>
  );
}

export default SettingsPage;
