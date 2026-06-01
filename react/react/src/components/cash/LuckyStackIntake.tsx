/**
 * LuckyStackIntake — the cold open of The Circuit (Act-1 onboarding).
 *
 * You stopped at a 50s diner, The Lucky Stack ("good hands served daily"), for
 * coffee; the waitress waves you toward "the back," assumes you're here for the
 * game, and comps you a stack. One snappy screen: give a name, then answer her
 * "tell me something about yourself" by picking one of a few replies. The room
 * LLM-christens you a tourist fish-name + a one-liner built off your reply, and
 * the reply is remembered (`intake_reply`) as a hook for later callbacks. Shown
 * only to a brand-new career player (`intake_needed`).
 *
 * The replies are plain character flavor — they don't map to any setting. The
 * newcomer doesn't (yet) know they've wandered into a poker room, so the lines
 * stay innocent: who you are, not how you'll play.
 *
 * Portaled to body (overlay must escape the page header's stacking context).
 */

import { useState } from 'react';
import { createPortal } from 'react-dom';
import { logger } from '../../utils/logger';
import { useAuth } from '../../hooks/useAuth';
import { DramaticReserve } from '../shared/DramaticText';
import { submitIntake, type IntakeResult } from './api';
import './LuckyStackIntake.css';

interface LuckyStackIntakeProps {
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

// A few replies to the waitress's "tell me something about yourself." Plain
// character flavor — no setting maps to them; they're just a line the player
// picks that the room remembers and can call back to later. The `reply` is the
// verbatim line they "say" (it feeds the bio); the `id` is a stable callback key.
// Deliberately INNOCENT of poker: the newcomer doesn't know what game's in back.
const REPLIES: { id: string; reply: string }[] = [
  {
    id: 'coffee',
    reply: "Honestly? Just followed my nose in for a decent cup of coffee.",
  },
  {
    id: 'game',
    reply: "Ah, I'm game for about anything once. Why not, right?",
  },
  {
    id: 'hard_to_read',
    reply: "Folks say I'm hard to read. Never did know what they meant by it.",
  },
];

export function LuckyStackIntake({ onDone }: LuckyStackIntakeProps) {
  const { user } = useAuth();
  // Pre-fill with the player's account name so they can just hit the button —
  // the box defaults to a real value, not a greyed-out placeholder. Editable.
  const [name, setName] = useState(() => user?.name ?? '');
  const [replyId, setReplyId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<IntakeResult | null>(null);

  const tellHer = async () => {
    const choice = REPLIES.find((r) => r.id === replyId);
    if (busy || !choice) return;
    setBusy(true);
    try {
      // Empty name → the server falls back to the account name (never "Stranger").
      const res = await submitIntake(name.trim(), choice.reply, choice.id);
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

            <label className="lucky__label" htmlFor="lucky-name">Name for the book</label>
            <input
              id="lucky-name"
              className="lucky__input"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="What should I call ya?"
              maxLength={40}
              autoFocus
            />

            <p className="lucky__prompt">“So — tell me somethin' about yourself, hon.”</p>
            <div className="lucky__deals">
              {REPLIES.map((opt) => (
                <button
                  key={opt.id}
                  type="button"
                  className={`lucky__deal${replyId === opt.id ? ' is-selected' : ''}`}
                  onClick={() => setReplyId(opt.id)}
                >
                  <span className="lucky__deal-reply">“{opt.reply}”</span>
                </button>
              ))}
            </div>

            <button className="lucky__btn" onClick={tellHer} disabled={busy || !replyId}>
              {busy ? 'She writes it down…' : 'Tell her'}
            </button>
          </>
        ) : (
          <div className="lucky__reveal">
            <div className="lucky__speech">
              <DramaticReserve key="scribble" text={WAITRESS_SCRIBBLE} />
            </div>
            <p className="lucky__fishname">“Fresh fish — {result.fish_name}!”</p>
            <div className="lucky__avatar" aria-hidden="true">🐟</div>
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
