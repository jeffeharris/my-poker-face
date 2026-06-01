/**
 * Arrival flavor for the cash-mode sit-down toast.
 *
 * The subtitle is derived from the player's REAL local clock, so sitting
 * down at "Murphy's Bar" on a Tuesday evening reads "Tuesday evening" —
 * the world feels live without any backend state. A table literally named
 * "Saturday Home Game" showing "Tuesday night" is intentional flavor: the
 * game is *called* the Saturday game; you wandered in on a Tuesday.
 */

/** "Tuesday evening" — weekday + time-of-day band from `now` (defaults to
 *  the current local time). Bands: <05 late night, <12 morning, <17
 *  afternoon, <21 evening, else night. */
export function arrivalSubtitle(now: Date = new Date()): string {
  const day = now.toLocaleDateString(undefined, { weekday: 'long' });
  const h = now.getHours();
  const band =
    h < 5 ? 'late night' : h < 12 ? 'morning' : h < 17 ? 'afternoon' : h < 21 ? 'evening' : 'night';
  return `${day} ${band}`;
}
