/**
 * Centralized timing constants for animations and effects.
 * Keeping these in one place makes it easier to tune the overall feel.
 */

// Chat/Dramatic sequence timings
export const TYPING_SPEED_MS = 30;           // ms per character for typing effect
export const READING_BUFFER_MS = 20;         // extra ms per character for reading time
export const ACTION_FADE_DURATION_MS = 400;  // fade in duration for action beats
export const BEAT_DELAY_MS = 300;            // delay between dramatic beats
export const QUEUED_MESSAGE_BONUS_MS = 1000; // extra time for queued messages

// Message display timings
export const MESSAGE_BASE_DURATION_MS = 2000;  // base time after animations
export const MESSAGE_MIN_DURATION_MS = 3000;   // minimum display duration
export const MESSAGE_MAX_DURATION_MS = 20000;  // maximum display duration

// Winner announcement timings
export const CARD_REVEAL_DELAY_MS = 800;
export const WINNER_DISMISS_MS = 8000;          // auto-dismiss (no showdown)
export const WINNER_DISMISS_SHOWDOWN_MS = 12000; // auto-dismiss (with showdown)
