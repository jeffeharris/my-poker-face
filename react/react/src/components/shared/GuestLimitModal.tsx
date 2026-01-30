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
    <div className="guest-limit-modal__overlay">
      <div className="guest-limit-modal">
        <div className="guest-limit-modal__icon">
          <Shield size={48} />
        </div>

        <h2 className="guest-limit-modal__title">
          You've played {handsPlayed} hands!
        </h2>

        <p className="guest-limit-modal__subtitle">
          Guest accounts are limited to {handsLimit} hands. Sign in with Google to keep playing with full access.
        </p>

        <div className="guest-limit-modal__benefits">
          <div className="guest-limit-modal__benefit">
            <Crown size={18} />
            <span>Unlimited hands</span>
          </div>
          <div className="guest-limit-modal__benefit">
            <Users size={18} />
            <span>Up to 9 AI opponents</span>
          </div>
          <div className="guest-limit-modal__benefit">
            <Settings size={18} />
            <span>Custom game wizard</span>
          </div>
          <div className="guest-limit-modal__benefit">
            <Sparkles size={18} />
            <span>Themed game experiences</span>
          </div>
        </div>

        <button className="guest-limit-modal__cta" onClick={handleSignIn}>
          <Crown size={18} />
          Sign in with Google
        </button>

        {onReturnToMenu && (
          <button className="guest-limit-modal__secondary" onClick={onReturnToMenu}>
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
