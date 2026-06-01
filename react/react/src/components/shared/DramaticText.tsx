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

import { useEffect, useState } from 'react';
import { TYPING_SPEED_MS, ACTION_FADE_DURATION_MS, BEAT_DELAY_MS } from '../../config/timing';
import { parseBeats } from '../../utils/chatBeats';
import './DramaticText.css';

/** Action beat — fades in (see each consumer's `.beat.action`/`.visible` CSS). */
function ActionBeat({ text, delay }: { text: string; delay: number }) {
  const [visible, setVisible] = useState(false);
  useEffect(() => {
    const timer = setTimeout(() => setVisible(true), delay);
    return () => clearTimeout(timer);
  }, [delay]);
  return (
    <div className={`beat action ${visible ? 'visible' : ''}`}>
      <em>{text}</em>
    </div>
  );
}

/** Speech beat — types out character by character after its `delay`. */
function SpeechBeat({ text, delay }: { text: string; delay: number }) {
  const [displayedText, setDisplayedText] = useState('');
  const [started, setStarted] = useState(false);

  useEffect(() => {
    const startTimer = setTimeout(() => setStarted(true), delay);
    return () => clearTimeout(startTimer);
  }, [delay]);

  useEffect(() => {
    if (!started) return;
    let charIndex = 0;
    const interval = setInterval(() => {
      if (charIndex < text.length) {
        setDisplayedText(text.slice(0, charIndex + 1));
        charIndex++;
      } else {
        clearInterval(interval);
      }
    }, TYPING_SPEED_MS);
    return () => clearInterval(interval);
  }, [started, text]);

  if (!started) return null;

  return (
    <div className="beat speech">
      {displayedText}
      {displayedText.length < text.length && <span className="typing-cursor">|</span>}
    </div>
  );
}

/** Render a message as sequenced, animated beats. */
export function DramaticMessage({ text }: { text: string }) {
  const beats = parseBeats(text);
  if (beats.length === 0) return <>{text}</>;

  let cumulativeDelay = 0;
  const beatsWithDelay = beats.map((beat, i) => {
    const delay = cumulativeDelay;
    if (beat.type === 'action') {
      cumulativeDelay += ACTION_FADE_DURATION_MS + BEAT_DELAY_MS;
    } else {
      cumulativeDelay += beat.text.length * TYPING_SPEED_MS + BEAT_DELAY_MS;
    }
    return { ...beat, delay, index: i };
  });

  return (
    <>
      {beatsWithDelay.map((beat) =>
        beat.type === 'action' ? (
          <ActionBeat key={beat.index} text={beat.text} delay={beat.delay} />
        ) : (
          <SpeechBeat key={beat.index} text={beat.text} delay={beat.delay} />
        )
      )}
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
