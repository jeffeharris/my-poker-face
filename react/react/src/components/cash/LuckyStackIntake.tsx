/**
 * LuckyStackIntake — the cold open of The Circuit (Act-1 onboarding).
 *
 * You stopped at a 50s diner, The Lucky Stack ("good hands served daily"), for
 * coffee; the waitress waves you toward "the back," assumes you're here for the
 * game, and comps you a stack. One snappy screen: give the name you want to go
 * by, then answer her "what's your story?" by picking one of three backgrounds.
 * The room christens you a tourist fish-name (rule-based) and the picked
 * background becomes your bio (`intake_reply`) + a hook for later callbacks.
 * Shown only to a brand-new career player (`intake_needed`).
 *
 * The three backgrounds are authored ONCE and arrive from the server (the
 * single source of truth), so there's no client copy to drift. They're plain
 * character flavor — innocent of poker (the newcomer doesn't yet know what
 * game's in back): who you are, not how you'll play.
 *
 * Portaled to body (overlay must escape the page header's stacking context).
 */

import { useState } from 'react';
import { createPortal } from 'react-dom';
import { logger } from '../../utils/logger';
import { useAuth } from '../../hooks/useAuth';
import { DramaticReserve } from '../shared/DramaticText';
import { submitIntake, type IntakeResult } from './api';
import type { IntakeBackstory } from './types';
import './LuckyStackIntake.css';

interface LuckyStackIntakeProps {
  /** The three authored backgrounds for Q2, from the lobby payload. */
  backstories: IntakeBackstory[];
  onDone: () => void;
}

// The waitress's lines, written as print-style BEATS (one per line): *asterisk*
// lines are stage directions that FADE in; the rest is speech that TYPES out.
const WAITRESS_INTRO = [
  "Mornin', hon. You here for the biscuits and gravy, or the game in the back?",
  '*She doesn’t wait for an answer — already sliding you a rack of chips.*',
  'House comps the first sit. What do I put in the book?',
].join('\n');

const WAITRESS_SCRIBBLE = '*She scribbles on her pad and hollers toward the back:*';

const WAITRESS_WELCOME = [
  '*She tips her head toward a near-empty back table — a couple regulars in it: some old-timer yappin’ over his coffee, and a wide-eyed fella across from him.*',
  'Go on, hon. They don’t bite. Much.',
].join('\n');

export function LuckyStackIntake({ backstories, onDone }: LuckyStackIntakeProps) {
  const { user } = useAuth();
  // Pre-fill with the player's account name so they can just hit the button —
  // the box defaults to a real value, not a greyed-out placeholder. Editable.
  const [name, setName] = useState(() => user?.name ?? '');
  const [backstoryId, setBackstoryId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<IntakeResult | null>(null);

  const tellHer = async () => {
    const choice = backstories.find((b) => b.id === backstoryId);
    if (busy || !choice) return;
    setBusy(true);
    try {
      // Empty name → the server falls back to the account name (never "Stranger").
      // The bio is resolved server-side by id; we send the text only for parity.
      const res = await submitIntake(name.trim(), choice.text, choice.id);
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
        {/* Centered above her speech — a clean vertical stack, not hovering aside. */}
        <img className="lucky__waitress-img" src="/waitress.png" alt="The Lucky Stack waitress" />

        {result === null ? (
          <>
            <div className="lucky__speech">
              <DramaticReserve key="intro" text={WAITRESS_INTRO} />
            </div>

            <label className="lucky__label" htmlFor="lucky-name">
              What should we call you?
            </label>
            <input
              id="lucky-name"
              className="lucky__input"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="The name you want to go by"
              maxLength={40}
              autoFocus
            />

            <p className="lucky__prompt">“So — what's your story, hon?”</p>
            <div className="lucky__deals">
              {backstories.map((opt) => (
                <button
                  key={opt.id}
                  type="button"
                  className={`lucky__deal${backstoryId === opt.id ? ' is-selected' : ''}`}
                  onClick={() => setBackstoryId(opt.id)}
                >
                  <span className="lucky__deal-title">{opt.title}</span>
                  <span className="lucky__deal-reply">{opt.text}</span>
                </button>
              ))}
            </div>

            <button className="lucky__btn" onClick={tellHer} disabled={busy || !backstoryId}>
              {busy ? 'She writes it down…' : 'Tell her'}
            </button>
          </>
        ) : (
          <div className="lucky__reveal">
            <div className="lucky__speech">
              <DramaticReserve key="scribble" text={WAITRESS_SCRIBBLE} />
            </div>
            <p className="lucky__fishname">“Fresh fish — {result.fish_name}!”</p>
            <div className="lucky__avatar" aria-hidden="true">
              🐟
            </div>
            {result.bio && <p className="lucky__bio">“{result.bio}”</p>}
            <div className="lucky__speech lucky__speech--welcome">
              <DramaticReserve key="welcome" text={WAITRESS_WELCOME} />
            </div>
            <button className="lucky__btn" onClick={onDone}>
              Head to the back
            </button>
          </div>
        )}
      </div>
    </div>,
    document.body
  );
}
