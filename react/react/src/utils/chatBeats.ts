import {
  TYPING_SPEED_MS,
  READING_BUFFER_MS,
  ACTION_FADE_DURATION_MS,
  BEAT_DELAY_MS,
  MESSAGE_BASE_DURATION_MS,
  MESSAGE_MIN_DURATION_MS,
  MESSAGE_MAX_DURATION_MS,
} from '../config/timing';

/**
 * Beat parsing + display-duration math shared by the chat surfaces (mobile
 * FloatingChat and the desktop SeatSpeechBubble). A "beat" is one line of a
 * message; lines wrapped in *asterisks* are stage directions (actions), the
 * rest is spoken speech.
 */
export interface ParsedBeat {
  type: 'action' | 'speech';
  text: string;
}

export function parseBeats(text: string): ParsedBeat[] {
  const lines = text.split('\n').filter((b) => b.trim());
  return lines.map((line) => {
    const actionMatch = line.match(/^\*(.+)\*$/);
    if (actionMatch) {
      return { type: 'action', text: actionMatch[1] };
    }
    return { type: 'speech', text: line };
  });
}

/** How long a bubble should stay up, scaled to typing + reading time. */
export function calculateDuration(message: string, action?: string): number {
  const trimmedMessage = message.trim();
  const trimmedAction = action?.trim() ?? '';
  const text = trimmedMessage.length > 0 ? trimmedMessage : trimmedAction;

  if (!text) return MESSAGE_MIN_DURATION_MS;

  const beats = parseBeats(text);
  let animationTime = 0;

  beats.forEach((beat, i) => {
    if (i > 0) animationTime += BEAT_DELAY_MS;

    if (beat.type === 'action') {
      animationTime += ACTION_FADE_DURATION_MS + beat.text.length * READING_BUFFER_MS;
    } else {
      animationTime += beat.text.length * (TYPING_SPEED_MS + READING_BUFFER_MS);
    }
  });

  const calculated = animationTime + MESSAGE_BASE_DURATION_MS;
  return Math.min(MESSAGE_MAX_DURATION_MS, Math.max(MESSAGE_MIN_DURATION_MS, calculated));
}
