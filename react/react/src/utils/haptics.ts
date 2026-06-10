import { isNativePlatform } from './nativeAuth';

/**
 * Native haptic feedback (iOS/Android) via @capacitor/haptics.
 *
 * No-op on the web and on any failure; the plugin is dynamically imported (and
 * cached) so it never enters the web bundle. Everything is fire-and-forget.
 *
 * iOS exposes only discrete taps (light/medium/heavy) and the three system
 * notification buzzes — there's no arbitrary waveform API. So distinctive
 * "feels" are built by SEQUENCING taps with millisecond offsets: a rising
 * crescendo, a double-tap, a heartbeat's lub-dub, etc. `hapticSequence` is that
 * engine; `HAPTICS` is the named palette assigned to game events.
 */

type ImpactStyle = 'light' | 'medium' | 'heavy';
type NotifyType = 'success' | 'warning' | 'error';

/** One tap in a sequence: a style, fired `at` milliseconds after sequence start. */
export interface HapticPulse {
  style: ImpactStyle;
  at: number;
}

// Cache the dynamic import so a multi-tap pattern doesn't re-import per pulse.
type CapHaptics = typeof import('@capacitor/haptics');
let modPromise: Promise<CapHaptics | null> | null = null;

function loadHaptics(): Promise<CapHaptics | null> | null {
  if (!isNativePlatform()) return null;
  if (!modPromise) {
    modPromise = import('@capacitor/haptics').catch(() => null);
  }
  return modPromise;
}

function fireImpact(mod: CapHaptics, style: ImpactStyle): void {
  const map = {
    light: mod.ImpactStyle.Light,
    medium: mod.ImpactStyle.Medium,
    heavy: mod.ImpactStyle.Heavy,
  } as const;
  void mod.Haptics.impact({ style: map[style] }).catch(() => {});
}

/** A single tactile tap. light = taps/selections, medium = call, heavy = all-in. */
export function hapticImpact(style: ImpactStyle = 'light'): void {
  const m = loadHaptics();
  if (!m) return;
  void m.then((mod) => mod && fireImpact(mod, style));
}

/** A notification buzz pattern (the OS's own success/warning/error cadence). */
export function hapticNotify(type: NotifyType = 'success'): void {
  const m = loadHaptics();
  if (!m) return;
  void m.then((mod) => {
    if (!mod) return;
    const map = {
      success: mod.NotificationType.Success,
      warning: mod.NotificationType.Warning,
      error: mod.NotificationType.Error,
    } as const;
    void mod.Haptics.notification({ type: map[type] }).catch(() => {});
  });
}

/**
 * Play a sequence of taps at their millisecond offsets — the building block for
 * every distinctive pattern. The plugin loads once; the first pulse fires
 * immediately, the rest are scheduled with setTimeout so the rhythm is felt.
 */
export function hapticSequence(pulses: HapticPulse[]): void {
  const m = loadHaptics();
  if (!m) return;
  void m.then((mod) => {
    if (!mod) return;
    for (const p of pulses) {
      if (p.at <= 0) fireImpact(mod, p.style);
      else window.setTimeout(() => fireImpact(mod, p.style), p.at);
    }
  });
}

/**
 * The haptic vocabulary — distinctive, named "feels" assigned to game beats.
 * Each is sequenced from discrete taps; the offsets ARE the personality.
 */
export const HAPTICS = {
  /** "You're up" — a quick rising two-tap (light → medium), ~easy to notice. */
  turn: () => hapticSequence([{ style: 'light', at: 0 }, { style: 'medium', at: 95 }]),

  /** A board card landing — a single soft tick. */
  boardCard: () => hapticImpact('light'),

  /** An opponent puts chips in (bet/raise) — one firm tap. */
  opponentRaise: () => hapticImpact('medium'),

  /** Crescendo: light → medium → heavy, tightening — drama of an all-in shove. */
  allIn: () =>
    hapticSequence([
      { style: 'light', at: 0 },
      { style: 'medium', at: 85 },
      { style: 'heavy', at: 185 },
    ]),

  /** Triumphant ascent — rising taps capped by a heavy on the win. */
  win: () =>
    hapticSequence([
      { style: 'light', at: 0 },
      { style: 'medium', at: 80 },
      { style: 'heavy', at: 175 },
    ]),

  /** Collapse — a heavy thud easing into a soft tail. */
  loss: () => hapticSequence([{ style: 'heavy', at: 0 }, { style: 'light', at: 150 }]),

  /** Rhythmic 3-tap alert (generic attention). */
  pulse: () =>
    hapticSequence([
      { style: 'light', at: 0 },
      { style: 'light', at: 130 },
      { style: 'light', at: 260 },
    ]),
} as const;

// --- Heartbeat loop -------------------------------------------------------
// A sustained "lub-dub" for tense, drawn-out moments (the all-in showdown
// run-out): medium then a slightly stronger beat ~130ms later, repeating a bit
// under once a second. Start when the sweat begins, stop when it resolves.
const HEARTBEAT_PERIOD_MS = 820;
let heartbeatTimer: number | null = null;

/** Begin the heartbeat loop (idempotent — a second call is a no-op). */
export function startHeartbeat(): void {
  if (!isNativePlatform() || heartbeatTimer !== null) return;
  const beat = () => hapticSequence([{ style: 'medium', at: 0 }, { style: 'heavy', at: 130 }]);
  beat(); // fire the first beat immediately, then settle into the rhythm
  heartbeatTimer = window.setInterval(beat, HEARTBEAT_PERIOD_MS);
}

/** Stop the heartbeat loop (safe to call when not running). */
export function stopHeartbeat(): void {
  if (heartbeatTimer !== null) {
    window.clearInterval(heartbeatTimer);
    heartbeatTimer = null;
  }
}
