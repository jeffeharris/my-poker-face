/**
 * LuckyStackIntake — the cold open of The Circuit (Act-1 onboarding).
 *
 * You stopped at a 50s diner, The Lucky Stack ("good hands served daily"), for
 * coffee; the waitress waves you toward "the back," assumes you're here for the
 * game, and comps you a stack. One snappy screen: give a name, then answer her
 * "tell me something about yourself" by picking one of three quick-chat replies
 * (this is where we introduce the quick-chat mechanic). The room LLM-christens
 * you a tourist fish-name + a one-liner built off your reply. Shown only to a
 * brand-new career player (`intake_needed`).
 *
 * The reply you pick also seeds your quick-chat default (`quickchat_intensity`)
 * and tone, so your in-game table-talk suggestions match how you introduced
 * yourself.
 *
 * Portaled to body (overlay must escape the page header's stacking context).
 */

import { useState } from 'react';
import { createPortal } from 'react-dom';
import { Flame, Handshake, Zap, type LucideIcon } from 'lucide-react';
import { logger } from '../../utils/logger';
import { submitIntake, type IntakeResult } from './api';
import './LuckyStackIntake.css';

interface LuckyStackIntakeProps {
  onDone: () => void;
}

// Three quick-chat replies to the waitress's "tell me something about yourself"
// — friendly → brutal. The `reply` is what you "say" (it feeds the room's bio of
// you, kept in sync with _INTAKE_ANSWERS in cash_mode/career_progression.py); the
// `id` is the quick-chat tone it seeds, `intensity` (chill/spicy) the quick-chat
// default, and `tone` labels the vibe.
const VIBES: {
  id: string;
  tone: string;
  reply: string;
  intensity: 'chill' | 'spicy';
  icon: LucideIcon;
}[] = [
  {
    id: 'befriend',
    tone: 'Friendly',
    reply: "Aw, I'm just here for a good time and a decent cup of coffee.",
    intensity: 'chill',
    icon: Handshake,
  },
  {
    id: 'needle',
    tone: 'Cocky',
    reply: "Honestly? You're lookin' at a natural. I pick things up quick.",
    intensity: 'spicy',
    icon: Zap,
  },
  {
    id: 'goad',
    tone: 'Ruthless',
    reply: "Came to take everybody's money. Nothin' personal, friend.",
    intensity: 'spicy',
    icon: Flame,
  },
];

export function LuckyStackIntake({ onDone }: LuckyStackIntakeProps) {
  const [name, setName] = useState('');
  const [vibeId, setVibeId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<IntakeResult | null>(null);

  const sitDown = async () => {
    const vibe = VIBES.find((v) => v.id === vibeId);
    if (busy || !vibe) return;
    setBusy(true);
    try {
      const res = await submitIntake(name.trim() || 'Stranger', vibe.intensity, vibe.id);
      // Seed the in-game quick-chat default to the vibe they picked here.
      try {
        localStorage.setItem('quickchat_intensity', res.intensity);
      } catch {
        /* private-mode / storage off — non-fatal */
      }
      setResult(res);
    } catch (e) {
      logger.error('intake failed:', e instanceof Error ? e.message : e);
      setBusy(false);
    }
  };

  return createPortal(
    <div className="lucky__overlay" role="dialog" aria-modal="true" aria-label="The Lucky Stack">
      <div className="lucky__card">
        <div className="lucky__sign">The Lucky Stack</div>
        <div className="lucky__sub">good hands served daily</div>

        {result === null ? (
          <>
            <div className="lucky__waitress">
              <img className="lucky__waitress-img" src="/waitress.png" alt="The Lucky Stack waitress" />
              <p className="lucky__line">
                “Mornin', hon. You here for the biscuits and gravy, or the game in the
                back?” <em>She doesn't wait for an answer — already sliding you a rack of
                chips.</em> “House comps the first sit. What do I put in the book?”
              </p>
            </div>

            <label className="lucky__label" htmlFor="lucky-name">Name</label>
            <input
              id="lucky-name"
              className="lucky__input"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Jeff"
              maxLength={40}
              autoFocus
            />

            <span className="lucky__label">
              “And tell me somethin' about yourself, hon.” <em>What do you say?</em>
            </span>
            <div className="lucky__deals">
              {VIBES.map((opt) => {
                const Icon = opt.icon;
                return (
                  <button
                    key={opt.id}
                    type="button"
                    className={`lucky__deal${vibeId === opt.id ? ' is-selected' : ''}`}
                    onClick={() => setVibeId(opt.id)}
                  >
                    <span className="lucky__deal-tone">
                      <Icon size={16} />
                      {opt.tone}
                    </span>
                    <span className="lucky__deal-reply">“{opt.reply}”</span>
                  </button>
                );
              })}
            </div>
            <p className="lucky__hint">
              Your answer sets your table-talk vibe too — switch it up any hand.
            </p>

            <button className="lucky__btn" onClick={sitDown} disabled={busy || !vibeId}>
              {busy ? 'Signing you in…' : 'Sit down'}
            </button>
          </>
        ) : (
          <div className="lucky__reveal">
            <div className="lucky__waitress">
              <img className="lucky__waitress-img" src="/waitress.png" alt="The Lucky Stack waitress" />
              <p className="lucky__line">
                <em>She scribbles on her pad and hollers toward the back:</em>
              </p>
            </div>
            <p className="lucky__fishname">“Fresh fish — {result.fish_name}!”</p>
            <div className="lucky__avatar" aria-hidden="true">🐟</div>
            {result.bio && <p className="lucky__bio">“{result.bio}”</p>}
            <div className="lucky__waitress">
              <img className="lucky__waitress-img" src="/waitress.png" alt="The Lucky Stack waitress" />
              <p className="lucky__line lucky__welcome">
                She tips her head toward a near-empty back table — plenty of open
                chairs, just a couple regulars in it: some old-timer yappin' away
                over his coffee, and a wide-eyed fella across from him.
                <em> “Go on, hon. They don't bite. Much.”</em>
              </p>
            </div>
            <button className="lucky__btn" onClick={onDone}>
              Take the seat
            </button>
          </div>
        )}
      </div>
    </div>,
    document.body
  );
}
