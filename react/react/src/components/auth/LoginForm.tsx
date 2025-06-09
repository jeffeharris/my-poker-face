import { useState } from 'react';
import { config } from '../../config';
import './LoginForm.css';

interface LoginFormProps {
  onLogin: (playerName: string, isGuest: boolean) => void;
  onCancel?: () => void;
}

export function LoginForm({ onLogin, onCancel }: LoginFormProps) {
  const [playerName, setPlayerName] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState('');

  const handleGuestLogin = async () => {
    if (!playerName.trim()) {
      setError('Please enter your name');
      return;
    }

    setIsLoading(true);
    setError('');

    try {
      const response = await fetch(`${config.API_URL}/api/auth/login`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          guest: true,
          name: playerName.trim()
        }),
      });

      const data = await response.json();

      if (data.success) {
        // Store token if provided
        if (data.token) {
          localStorage.setItem('authToken', data.token);
        }
        
        // Store user data
        localStorage.setItem('currentUser', JSON.stringify(data.user));
        
        onLogin(data.user.name, true);
      } else {
        setError(data.error || 'Login failed');
      }
    } catch (err) {
      setError('Connection error. Please try again.');
    } finally {
      setIsLoading(false);
    }
  };

  const handleKeyPress = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !isLoading) {
      handleGuestLogin();
    }
  };

  return (
    <div className="login-form">
      <div className="login-form__container">
        <h2>Welcome to My Poker Face</h2>
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
            disabled={true}
            title="Coming soon!"
          >
            <img src="/google-icon.svg" alt="Google" />
            Sign in with Google
          </button>

          <p className="login-form__note">
            Guest accounts are temporary. Sign in with Google to save your progress.
          </p>
        </div>
      </div>
    </div>
  );
}