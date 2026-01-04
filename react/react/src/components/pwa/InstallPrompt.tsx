import { useState, useEffect } from 'react';
import './InstallPrompt.css';

interface BeforeInstallPromptEvent extends Event {
  prompt: () => Promise<void>;
  userChoice: Promise<{ outcome: 'accepted' | 'dismissed' }>;
}

const DISMISSED_KEY = 'pwa-install-dismissed';
const DISMISS_DURATION = 7 * 24 * 60 * 60 * 1000; // 7 days

export function InstallPrompt() {
  const [installPrompt, setInstallPrompt] = useState<BeforeInstallPromptEvent | null>(null);
  const [isVisible, setIsVisible] = useState(false);

  useEffect(() => {
    // Check if user previously dismissed the prompt
    const dismissedAt = localStorage.getItem(DISMISSED_KEY);
    if (dismissedAt) {
      const dismissedTime = parseInt(dismissedAt, 10);
      if (Date.now() - dismissedTime < DISMISS_DURATION) {
        return; // Still within dismiss period
      }
      localStorage.removeItem(DISMISSED_KEY);
    }

    const handleBeforeInstallPrompt = (e: Event) => {
      console.log('[PWA] beforeinstallprompt event fired!');
      e.preventDefault();
      setInstallPrompt(e as BeforeInstallPromptEvent);
      setIsVisible(true);
    };

    console.log('[PWA] InstallPrompt mounted, listening for beforeinstallprompt...');

    window.addEventListener('beforeinstallprompt', handleBeforeInstallPrompt);

    return () => {
      window.removeEventListener('beforeinstallprompt', handleBeforeInstallPrompt);
    };
  }, []);

  const handleInstall = async () => {
    if (!installPrompt) return;

    await installPrompt.prompt();
    const { outcome } = await installPrompt.userChoice;

    if (outcome === 'accepted') {
      setIsVisible(false);
      setInstallPrompt(null);
    }
  };

  const handleDismiss = () => {
    setIsVisible(false);
    localStorage.setItem(DISMISSED_KEY, Date.now().toString());
  };

  if (!isVisible || !installPrompt) {
    return null;
  }

  return (
    <div className="install-prompt">
      <div className="install-prompt__content">
        <img
          src="/icon-192x192.png"
          alt="My Poker Face"
          className="install-prompt__icon"
        />
        <div className="install-prompt__text">
          <strong>Install My Poker Face</strong>
          <span>Add to home screen for the best experience</span>
        </div>
      </div>
      <div className="install-prompt__actions">
        <button
          onClick={handleDismiss}
          className="install-prompt__button install-prompt__button--dismiss"
        >
          Not now
        </button>
        <button
          onClick={handleInstall}
          className="install-prompt__button install-prompt__button--install"
        >
          Install
        </button>
      </div>
    </div>
  );
}
