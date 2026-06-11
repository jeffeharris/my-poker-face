/**
 * Capture the device's bottom safe-area inset into a stable CSS variable.
 *
 * On iOS with the Capacitor keyboard in `resize: Native` mode, the WebView frame
 * shrinks while the keyboard is up and `env(safe-area-inset-bottom)` collapses to
 * 0 — and it doesn't always restore cleanly on dismiss, so bottom-anchored UI
 * (the action bar) ends up flush with the screen edge, clipped by the home
 * indicator. We measure the real inset once while the keyboard is closed and
 * publish it as `--app-safe-bottom`, which those elements use instead of the live
 * (collapsible) `env()`. The value only changes on rotation, so re-capturing on
 * orientation change keeps it correct without ever reading a keyboard-collapsed 0.
 */

let scheduled = false;

function measure(): void {
  scheduled = false;
  if (typeof document === 'undefined' || !document.body) return;
  const probe = document.createElement('div');
  probe.style.cssText =
    'position:fixed;left:0;bottom:0;width:0;visibility:hidden;pointer-events:none;' +
    'height:env(safe-area-inset-bottom);';
  document.body.appendChild(probe);
  const bottom = probe.getBoundingClientRect().height;
  document.body.removeChild(probe);
  // Guard against capturing a transient 0 (e.g. measured while a keyboard is up):
  // never overwrite a known-good inset with 0. On web the inset is genuinely 0,
  // so the variable simply stays unset and CSS falls back to env().
  if (bottom > 0) {
    document.documentElement.style.setProperty('--app-safe-bottom', `${bottom}px`);
  }
}

/** Install the capture: once now, and again after each orientation change. */
export function initSafeAreaCapture(): void {
  if (typeof window === 'undefined') return;
  const schedule = () => {
    if (scheduled) return;
    scheduled = true;
    // Defer so layout/insets have settled (rotation needs a beat to stabilize).
    requestAnimationFrame(() => requestAnimationFrame(measure));
  };
  schedule();
  window.addEventListener('orientationchange', schedule);
}
