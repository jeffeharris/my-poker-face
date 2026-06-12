import { Coins, Trophy, GraduationCap, ChevronRight } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { isNativePlatform } from '../../utils/nativeAuth';
import { PageLayout, PageHeader, MenuBar } from '../shared';
import { useViewport } from '../../hooks/useViewport';
import menuBanner from '../../assets/menu-banner.webp';
import './HomeMenu.css';

interface HomeMenuProps {
  playerName: string;
  onCashMode: () => void;
  onTournament: () => void;
  onTraining: () => void;
  onAdminDashboard?: () => void;
}

export function HomeMenu({
  playerName,
  onCashMode,
  onTournament,
  onTraining,
  onAdminDashboard,
}: HomeMenuProps) {
  const { isDesktop } = useViewport();
  const navigate = useNavigate();

  return (
    <>
      <MenuBar showUserInfo onAdminTools={onAdminDashboard} />
      <PageLayout variant="top" glowColor="gold" maxWidth={isDesktop ? 'md' : 'md'} hasMenuBar>
        <div className="home-menu__banner">
          <img
            src={menuBanner}
            alt="My Poker Face"
            className="home-menu__banner-image"
            width={760}
            height={313}
            decoding="async"
            fetchPriority="high"
          />
        </div>

        <PageHeader
          title={`Welcome, ${playerName}!`}
          subtitle="How are you playing today?"
          titleVariant="primary"
        />

        <div className="home-menu__modes">
          <button className="mode-card" onClick={onCashMode}>
            <div className="mode-card__icon-wrap">
              <Coins className="mode-card__icon" size={64} strokeWidth={1.5} />
            </div>
            <div className="mode-card__content">
              <h2 className="mode-card__title">The Circuit</h2>
              <p className="mode-card__description">
                Pick a stake, sit at a table, build a bankroll
              </p>
            </div>
            <ChevronRight className="mode-card__arrow" size={22} />
          </button>

          <button className="mode-card" onClick={onTournament}>
            <div className="mode-card__icon-wrap">
              <Trophy className="mode-card__icon" size={64} strokeWidth={1.5} />
            </div>
            <div className="mode-card__content">
              <h2 className="mode-card__title">Tournaments</h2>
              <p className="mode-card__description">Single table, winner takes all</p>
            </div>
            <ChevronRight className="mode-card__arrow" size={22} />
          </button>

          <button className="mode-card" onClick={onTraining}>
            <div className="mode-card__icon-wrap">
              <GraduationCap className="mode-card__icon" size={64} strokeWidth={1.5} />
            </div>
            <div className="mode-card__content">
              <h2 className="mode-card__title">Practice</h2>
              <p className="mode-card__description">
                Learn with a coach — easy or hard, nothing counts
              </p>
            </div>
            <ChevronRight className="mode-card__arrow" size={22} />
          </button>
        </div>

        {/* TEMP (on-device LLM spike): native-only so prod web never shows it. */}
        {isNativePlatform() && (
          <button
            onClick={() => navigate('/dev/fmtest')}
            style={{
              marginTop: 16,
              background: 'transparent',
              border: '1px dashed #888',
              color: '#aaa',
              padding: '8px 14px',
              borderRadius: 8,
              fontSize: 13,
            }}
          >
            🧪 On-device LLM test
          </button>
        )}
      </PageLayout>
    </>
  );
}
