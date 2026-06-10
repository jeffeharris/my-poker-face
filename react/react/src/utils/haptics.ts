import { isNativePlatform } from './nativeAuth';

/**
 * Native haptic feedback (iOS/Android) via @capacitor/haptics.
 *
 * No-op on the web and on any failure, and the plugin is dynamically imported so
 * it never enters the web bundle. Fire-and-forget: callers don't await.
 */

type ImpactStyle = 'light' | 'medium' | 'heavy';
type NotifyType = 'success' | 'warning' | 'error';

/** A tactile tap. light = taps/selections, medium = call, heavy = all-in. */
export function hapticImpact(style: ImpactStyle = 'light'): void {
  if (!isNativePlatform()) return;
  void (async () => {
    try {
      const { Haptics, ImpactStyle: Style } = await import('@capacitor/haptics');
      const map = {
        light: Style.Light,
        medium: Style.Medium,
        heavy: Style.Heavy,
      } as const;
      await Haptics.impact({ style: map[style] });
    } catch {
      /* haptics unavailable — ignore */
    }
  })();
}

/** A notification buzz pattern (win/lose/illegal-move feedback). */
export function hapticNotify(type: NotifyType = 'success'): void {
  if (!isNativePlatform()) return;
  void (async () => {
    try {
      const { Haptics, NotificationType } = await import('@capacitor/haptics');
      const map = {
        success: NotificationType.Success,
        warning: NotificationType.Warning,
        error: NotificationType.Error,
      } as const;
      await Haptics.notification({ type: map[type] });
    } catch {
      /* haptics unavailable — ignore */
    }
  })();
}
