import { Crown, ChevronRight } from 'lucide-react';
import { config } from '../../config';
import './UpgradeBanner.css';

export interface UpgradeBannerProps {
  variant?: 'compact' | 'full';
}

export function UpgradeBanner({ variant = 'compact' }: UpgradeBannerProps) {
  const handleUpgrade = () => {
    window.location.href = `${config.API_URL}/api/auth/google/login`;
  };

  if (variant === 'compact') {
    return (
      <button className="upgrade-banner upgrade-banner--compact" onClick={handleUpgrade}>
        <Crown size={16} className="upgrade-banner__icon" />
        <span className="upgrade-banner__text">Sign in with Google for unlimited games</span>
        <ChevronRight size={16} className="upgrade-banner__arrow" />
      </button>
    );
  }

  return (
    <div className="upgrade-banner upgrade-banner--full">
      <div className="upgrade-banner__header">
        <Crown size={20} className="upgrade-banner__icon" />
        <h4 className="upgrade-banner__title">Unlock Full Access</h4>
      </div>
      <ul className="upgrade-banner__benefits">
        <li>Unlimited hands per session</li>
        <li>Up to 9 AI opponents</li>
        <li>Custom game wizard</li>
        <li>Themed game experiences</li>
        <li>Multiple saved games</li>
      </ul>
      <button className="upgrade-banner__cta" onClick={handleUpgrade}>
        <Crown size={16} />
        Sign in with Google
      </button>
    </div>
  );
}
