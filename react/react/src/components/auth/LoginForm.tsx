import { useState } from 'react';
import { PageLayout } from '../shared';
import { config } from '../../config';
import menuBanner from '../../assets/menu-banner.png';
import './LoginForm.css';

interface LoginFormProps {
  onLogin: (playerName: string, isGuest: boolean) => void;
  onCancel?: () => void;
}

export function LoginForm({ onLogin, onCancel }: LoginFormProps) {
  const [playerName, setPlayerName] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [isGoogleLoading, setIsGoogleLoading] = useState(false);
  const [error, setError] = useState('');

  const handleGuestLogin = async () => {
    if (!playerName.trim()) {
      setError('Please enter your name');
      return;
    }

    setIsLoading(true);
    setError('');

    try {
      // Call the parent's onLogin handler which will handle the API call
      await onLogin(playerName.trim(), true);
      // If we reach here without error, login was successful
    } catch {
      setError('Connection error. Please try again.');
      setIsLoading(false);
    }
  };

  const handleKeyPress = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !isLoading) {
      handleGuestLogin();
    }
  };

  const handleGoogleLogin = () => {
    setIsGoogleLoading(true);
    setError('');
    // Redirect to backend Google OAuth endpoint
    window.location.href = `${config.API_URL}/api/auth/google/login`;
  };

  return (
    <PageLayout variant="centered" glowColor="gold" maxWidth="sm">
      <div className="login-form__container">
        {/* Banner */}
        <div className="login-form__banner">
          <img src={menuBanner} alt="My Poker Face" className="login-form__banner-image" />
        </div>

        <p className="login-form__subtitle">Enter your name to start playing</p>

        <div className="login-form__content">
          <input
            type="text"
            value={playerName}
            onChange={(e) => setPlayerName(e.target.value)}
            onKeyPress={handleKeyPress}
            placeholder="Your name"
            className="login-form__input"
            autoFocus
            disabled={isLoading}
            maxLength={20}
          />

          {error && (
            <div className="login-form__error">
              {error}
            </div>
          )}

          <div className="login-form__actions">
            <button
              onClick={handleGuestLogin}
              disabled={isLoading || !playerName.trim()}
              className="login-form__button login-form__button--primary"
            >
              {isLoading ? 'Logging in...' : 'Play as Guest'}
            </button>

            {onCancel && (
              <button
                onClick={onCancel}
                disabled={isLoading}
                className="login-form__button login-form__button--secondary"
              >
                Cancel
              </button>
            )}
          </div>

          <div className="login-form__divider">
            <span>or</span>
          </div>

          <button
            className="login-form__button login-form__button--google"
            onClick={handleGoogleLogin}
            disabled={isLoading || isGoogleLoading}
          >
            <svg width="18" height="18" viewBox="0 0 24 24" style={{ marginRight: '8px' }}>
              <path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"/>
              <path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"/>
              <path fill="#FBBC05" d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z"/>
              <path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"/>
            </svg>
            {isGoogleLoading ? 'Redirecting...' : 'Sign in with Google'}
          </button>

          <p className="login-form__note">
            Guest accounts are temporary. Sign in with Google to save your progress.
          </p>
        </div>

        <footer className="login-form__footer">
          <a href="/privacy" target="_blank" rel="noopener noreferrer">Privacy Policy</a>
          <span className="login-form__footer-divider">·</span>
          <a href="/terms" target="_blank" rel="noopener noreferrer">Terms of Service</a>
        </footer>
      </div>
    </PageLayout>
  );
}