import { isNativePlatform } from './nativeAuth';

/**
 * Native haptic feedback (iOS/Android) via @capacitor/haptics.
 *
 * No-op on the web and on any failure; the plugin is dynamically imported (and
 * cached) so it never enters the web bundle. Everything is fire-and-forget.
 *
 * Three mechanisms, increasing in strength:
 *   - impact(light|medium|heavy): the Taptic Engine's crisp taps (refined/subtle)
 *   - notification(success|warning|error): the OS's stronger multi-pulse buzzes
 *   - vibrate({duration}): the actual vibration MOTOR — the punchiest, a real buzz
 * Distinctive "feels" are built by SEQUENCING these at millisecond offsets (a
 * rising ramp, a double-knock, a heartbeat's lub-dub). `hapticSequence` is that
 * engine; `HAPTICS` is the named palette assigned to game events.
 */

type ImpactStyle = 'light' | 'medium' | 'heavy';
type NotifyType = 'success' | 'warning' | 'error';

/**
 * One step in a sequence, fired `at` milliseconds after sequence start. Either a
 * Taptic tap (`style`) OR a vibration-motor buzz (`vibrateMs`, the punchier one).
 */
export interface HapticPulse {
  at: number;
  style?: ImpactStyle;
  vibrateMs?: number;
}

// Cache the dynamic import so a multi-step pattern doesn't re-import per pulse.
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

function firePulse(mod: CapHaptics, p: HapticPulse): void {
  if (p.vibrateMs != null) void mod.Haptics.vibrate({ duration: p.vibrateMs }).catch(() => {});
  else if (p.style) fireImpact(mod, p.style);
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

/** A raw vibration-motor buzz of `durationMs` — the strongest single cue. */
export function hapticVibrate(durationMs = 300): void {
  const m = loadHaptics();
  if (!m) return;
  void m.then((mod) => mod && void mod.Haptics.vibrate({ duration: durationMs }).catch(() => {}));
}

/**
 * Play a sequence of pulses (taps and/or motor buzzes) at their millisecond
 * offsets — the building block for every distinctive pattern. The plugin loads
 * once; the first pulse fires immediately, the rest are scheduled so the rhythm
 * is felt.
 */
export function hapticSequence(pulses: HapticPulse[]): void {
  const m = loadHaptics();
  if (!m) return;
  void m.then((mod) => {
    if (!mod) return;
    for (const p of pulses) {
      if (p.at <= 0) firePulse(mod, p);
      else window.setTimeout(() => firePulse(mod, p), p.at);
    }
  });
}

/**
 * The haptic vocabulary — distinctive, named "feels" assigned to game beats.
 * Tuned punchy: taps run a tier hot, the motor (`vibrateMs`) lands on the big
 * beats, and results use the OS notification buzzes.
 */
export const HAPTICS = {
  /** "You're up" — leads with a sustained MOTOR buzz (no other cue does), then a
   *  rising double-tap. Deliberately unmistakable and unlike every other cue. */
  turn: () =>
    hapticSequence([
      { vibrateMs: 350, at: 0 },
      { style: 'medium', at: 400 },
      { style: 'heavy', at: 500 },
    ]),

  /** A board card landing — one firm tap. */
  boardCard: () => hapticImpact('medium'),

  /** An opponent checks or calls — a plain "double knock" (two taps). */
  knock: () =>
    hapticSequence([
      { style: 'medium', at: 0 },
      { style: 'medium', at: 110 },
    ]),

  /** An opponent raises — an INCREASING-intensity ramp capped by a motor buzz. */
  opponentRaise: () =>
    hapticSequence([
      { style: 'light', at: 0 },
      { style: 'medium', at: 70 },
      { style: 'heavy', at: 150 },
      { vibrateMs: 220, at: 245 },
    ]),

  /** An all-in shove — a bigger crescendo into a long motor buzz (the most intense). */
  allIn: () =>
    hapticSequence([
      { style: 'medium', at: 0 },
      { style: 'heavy', at: 90 },
      { vibrateMs: 450, at: 200 },
    ]),

  /** You win — the OS success buzz, then a triumphant motor kick. */
  win: () => {
    hapticNotify('success');
    window.setTimeout(() => hapticVibrate(400), 180);
  },

  /** You lose — the OS error buzz, then a heavy thud. */
  loss: () => {
    hapticNotify('error');
    window.setTimeout(() => hapticImpact('heavy'), 200);
  },

  /** Rhythmic 3-tap alert (generic attention; unassigned spare). */
  pulse: () =>
    hapticSequence([
      { style: 'medium', at: 0 },
      { style: 'medium', at: 130 },
      { style: 'medium', at: 260 },
    ]),
} as const;

// --- Heartbeat loop -------------------------------------------------------
// A sustained "lub-dub" for tense, drawn-out moments (the all-in showdown
// run-out): a heavy tap (lub) then a short motor buzz (DUB) ~130ms later so it
// actually thumps, repeating a bit under once a second. Start when the sweat
// begins, stop when it resolves.
const HEARTBEAT_PERIOD_MS = 820;
let heartbeatTimer: number | null = null;

/** Begin the heartbeat loop (idempotent — a second call is a no-op). */
export function startHeartbeat(): void {
  if (!isNativePlatform() || heartbeatTimer !== null) return;
  const beat = () =>
    hapticSequence([
      { style: 'heavy', at: 0 },
      { vibrateMs: 200, at: 130 },
    ]);
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
