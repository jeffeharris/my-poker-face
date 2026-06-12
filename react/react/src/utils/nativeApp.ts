import { isNativePlatform } from './nativeAuth';

/**
 * Native app-lifecycle helpers (iOS/Android) via @capacitor/app.
 *
 * The plugin is dynamically imported so it never enters the web bundle, and
 * everything no-ops on the web. We use this instead of the DOM
 * `visibilitychange` event because that event is unreliable in an iOS WKWebView
 * across lock / app-switch / control-center transitions — `appStateChange` is
 * the Capacitor-supported foreground/background signal.
 */

/**
 * Run `cb` whenever the native app returns to the foreground (resume). Returns a
 * disposer that removes the listener. No-op on web (disposer is a no-op).
 */
export function onAppResume(cb: () => void): () => void {
  if (!isNativePlatform()) return () => {};
  let remove: (() => void) | null = null;
  let cancelled = false;
  void (async () => {
    try {
      const { App } = await import('@capacitor/app');
      const handle = await App.addListener('appStateChange', ({ isActive }) => {
        if (isActive) cb();
      });
      // The effect may have torn down before the async import resolved.
      if (cancelled) handle.remove();
      else remove = () => handle.remove();
    } catch {
      /* plugin unavailable — ignore */
    }
  })();
  return () => {
    cancelled = true;
    remove?.();
  };
}
