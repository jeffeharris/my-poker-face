import { Coins, Trophy, ChevronRight } from 'lucide-react';
import { PageLayout, PageHeader, MenuBar } from '../shared';
import { useViewport } from '../../hooks/useViewport';
import menuBanner from '../../assets/menu-banner.webp';
import './HomeMenu.css';

interface HomeMenuProps {
  playerName: string;
  onCashMode: () => void;
  onTournament: () => void;
  onAdminDashboard?: () => void;
}

export function HomeMenu({
  playerName,
  onCashMode,
  onTournament,
  onAdminDashboard,
}: HomeMenuProps) {
  const { isDesktop } = useViewport();

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
              <h2 className="mode-card__title">Career</h2>
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
        </div>
      </PageLayout>
    </>
  );
}
