import { useNavigate } from 'react-router-dom';
import { Monitor, MessageCircle, Brain, Flame } from 'lucide-react';
import { PageLayout } from '../shared';
import menuBanner from '../../assets/menu-banner.png';
import './LandingPage.css';

export function LandingPage() {
  const navigate = useNavigate();

  return (
    <PageLayout variant="centered" glowColor="gold" maxWidth="md">
      <div className="landing">
        {/* Banner */}
        <div className="landing__banner">
          <img src={menuBanner} alt="My Poker Face" className="landing__banner-image" />
        </div>

        {/* Hero Section */}
        <header className="landing__hero">
          <p className="landing__tagline">
            Poker against AI that feels human - emotions, rivalries, and mind games
          </p>
        </header>

        {/* Features */}
        <div className="landing__features">
          <div className="landing__feature">
            <div className="landing__feature-icon">
              <Brain size={24} />
            </div>
            <h3>Real Personalities</h3>
            <p>Each opponent has a distinct personality - they remember how you play and form rivalries with each other</p>
          </div>

          <div className="landing__feature">
            <div className="landing__feature-icon">
              <Flame size={24} />
            </div>
            <h3>Emotions Matter</h3>
            <p>Opponents can go on tilt after a bad beat, get rattled by big losses, or play erratically when frustrated</p>
          </div>

          <div className="landing__feature">
            <div className="landing__feature-icon">
              <MessageCircle size={24} />
            </div>
            <h3>Table Talk</h3>
            <p>Chat with your opponents - goad them into calling, get under their skin, or just enjoy the banter</p>
          </div>

          <div className="landing__feature">
            <div className="landing__feature-icon">
              <Monitor size={24} />
            </div>
            <h3>Play Anywhere</h3>
            <p>Pick up your game anytime - your opponents are waiting, and they remember where you left off</p>
          </div>
        </div>

        {/* CTA */}
        <div className="landing__cta">
          <button
            className="landing__button landing__button--primary"
            onClick={() => navigate('/login')}
          >
            Play Now
          </button>
        </div>

        {/* Footer */}
        <footer className="landing__footer">
          <a href="/privacy.html">Privacy Policy</a>
          <span className="landing__footer-divider">·</span>
          <a href="/terms.html">Terms of Service</a>
        </footer>
      </div>
    </PageLayout>
  );
}
