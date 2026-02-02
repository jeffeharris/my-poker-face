import { Crown, Shield, Users, Sparkles, Settings } from 'lucide-react';
import { config } from '../../config';
import './GuestLimitModal.css';

export interface GuestLimitModalProps {
  handsPlayed: number;
  handsLimit: number;
  onReturnToMenu?: () => void;
}

export function GuestLimitModal({ handsPlayed, handsLimit, onReturnToMenu }: GuestLimitModalProps) {
  const handleSignIn = () => {
    window.location.href = `${config.API_URL}/api/auth/google/login`;
  };

  return (
    <div className="guest-limit-modal__overlay" data-testid="guest-limit-overlay">
      <div className="guest-limit-modal" data-testid="guest-limit-modal">
        <div className="guest-limit-modal__icon" data-testid="guest-limit-icon">
          <Shield size={48} />
        </div>

        <h2 className="guest-limit-modal__title" data-testid="guest-limit-title">
          You've played {handsPlayed} hands!
        </h2>

        <p className="guest-limit-modal__subtitle">
          Guest accounts are limited to {handsLimit} hands. Sign in with Google to keep playing with full access.
        </p>

        <div className="guest-limit-modal__benefits">
          <div className="guest-limit-modal__benefit" data-testid="guest-limit-benefit">
            <Crown size={18} />
            <span>Unlimited hands</span>
          </div>
          <div className="guest-limit-modal__benefit" data-testid="guest-limit-benefit">
            <Users size={18} />
            <span>Up to 9 AI opponents</span>
          </div>
          <div className="guest-limit-modal__benefit" data-testid="guest-limit-benefit">
            <Settings size={18} />
            <span>Custom game wizard</span>
          </div>
          <div className="guest-limit-modal__benefit" data-testid="guest-limit-benefit">
            <Sparkles size={18} />
            <span>Themed game experiences</span>
          </div>
        </div>

        <button className="guest-limit-modal__cta" data-testid="guest-limit-cta" onClick={handleSignIn}>
          <Crown size={18} />
          Sign in with Google
        </button>

        {onReturnToMenu && (
          <button className="guest-limit-modal__secondary" data-testid="guest-limit-secondary" onClick={onReturnToMenu}>
            Return to Main Menu
          </button>
        )}

        <p className="guest-limit-modal__note">
          Your game progress and stats will be preserved.
        </p>
      </div>
    </div>
  );
}
