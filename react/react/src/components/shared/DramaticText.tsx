/**
 * DramaticText — the shared "print style" beat renderer.
 *
 * One message is split into beats (one per line). Lines wrapped in *asterisks*
 * are stage directions / filler that FADE IN; everything else is speech that
 * TYPES OUT character-by-character. Beats animate in sequence with a small pause
 * between them, so a multi-line message reads like it's being performed.
 *
 * Used by the mobile FloatingChat, the desktop SeatSpeechBubble, Sal's mentor
 * floater, and the Lucky Stack intake. Each consumer styles the `.beat.action`,
 * `.beat.speech`, and `.typing-cursor` classes under its own bubble container.
 *
 * To get a beat/pause after every sentence without hand-authoring newlines, run
 * the text through `splitSentences` (in `utils/chatBeats`) first.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { TYPING_SPEED_MS, ACTION_FADE_DURATION_MS, BEAT_DELAY_MS } from '../../config/timing';
import { parseBeats } from '../../utils/chatBeats';
import './DramaticText.css';

/** Keep the latest `onDone` in a ref so a beat's animation effect (which must run
 *  exactly once per mount) doesn't restart when the parent flips `onDone` on/off
 *  as beats become/stop being the active one. */
function useDoneRef(onDone?: () => void) {
  const ref = useRef(onDone);
  ref.current = onDone;
  return ref;
}

/** Action beat — fades in, then signals `onDone` when the fade completes. */
function ActionBeat({ text, onDone }: { text: string; onDone?: () => void }) {
  const [visible, setVisible] = useState(false);
  const doneRef = useDoneRef(onDone);
  useEffect(() => {
    const show = setTimeout(() => setVisible(true), 20); // next frame → CSS transition
    const done = setTimeout(() => doneRef.current?.(), ACTION_FADE_DURATION_MS);
    return () => {
      clearTimeout(show);
      clearTimeout(done);
    };
    // Runs once per mount — `text` identifies the beat; doneRef stays current.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [text]);
  return (
    <div className={`beat action ${visible ? 'visible' : ''}`}>
      <em>{text}</em>
    </div>
  );
}

/** Speech beat — types out character by character, then signals `onDone`. */
function SpeechBeat({ text, onDone }: { text: string; onDone?: () => void }) {
  const [displayedText, setDisplayedText] = useState('');
  const doneRef = useDoneRef(onDone);
  useEffect(() => {
    let charIndex = 0;
    const interval = setInterval(() => {
      charIndex++;
      setDisplayedText(text.slice(0, charIndex));
      if (charIndex >= text.length) {
        clearInterval(interval);
        doneRef.current?.();
      }
    }, TYPING_SPEED_MS);
    return () => clearInterval(interval);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [text]);
  return (
    <div className="beat speech">
      {displayedText}
      {displayedText.length < text.length && <span className="typing-cursor">|</span>}
    </div>
  );
}

/** Render a message as sequenced, animated beats. The next beat only STARTS once
 *  the previous one has actually finished (event-driven, not pre-computed delays —
 *  so typing-speed drift can't make beats overlap), with a short pause between. */
export function DramaticMessage({ text }: { text: string }) {
  const beats = parseBeats(text);
  const [revealed, setRevealed] = useState(0); // index of the currently-animating beat
  const timerRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  // Restart from the top if the text changes (usually a key change remounts us,
  // but this also covers an in-place text swap); always clear a pending advance.
  useEffect(() => {
    setRevealed(0);
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, [text]);

  // A beat finished → pause, then mount the next one.
  const advance = useCallback(() => {
    timerRef.current = setTimeout(() => setRevealed((r) => r + 1), BEAT_DELAY_MS);
  }, []);

  if (beats.length === 0) return <>{text}</>;

  return (
    <>
      {beats.slice(0, revealed + 1).map((beat, i) => {
        // Only the active (last-revealed) beat advances the sequence; earlier
        // beats are settled and pass no onDone (so they never re-fire).
        const onDone = i === revealed && i < beats.length - 1 ? advance : undefined;
        return beat.type === 'action' ? (
          <ActionBeat key={i} text={beat.text} onDone={onDone} />
        ) : (
          <SpeechBeat key={i} text={beat.text} onDone={onDone} />
        );
      })}
    </>
  );
}

/** DramaticMessage that RESERVES its final height up front so the container
 *  doesn't grow/jitter as beats type in. Renders a hidden full-text ghost (which
 *  sets the box height using the same `.beat` styles) with the live animation
 *  absolutely positioned over it. Drop it wherever the typing would otherwise
 *  reflow the layout. */
export function DramaticReserve({ text }: { text: string }) {
  const beats = parseBeats(text);
  return (
    <div className="dramatic-reserve">
      <div className="dramatic-reserve__ghost" aria-hidden="true">
        {beats.map((beat, i) => (
          <div key={i} className={beat.type === 'action' ? 'beat action visible' : 'beat speech'}>
            {beat.type === 'action' ? <em>{beat.text}</em> : beat.text}
          </div>
        ))}
      </div>
      <div className="dramatic-reserve__live">
        <DramaticMessage text={text} />
      </div>
    </div>
  );
}
