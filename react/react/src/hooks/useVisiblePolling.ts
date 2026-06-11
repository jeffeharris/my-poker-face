import { useEffect } from 'react';

/**
 * Run `callback` on mount and then every `intervalMs`, but ONLY while the
 * document is visible.
 *
 * When the tab is hidden the interval is paused — a dashboard left open in a
 * background tab otherwise polls the server forever, which on a single-worker
 * backend is real, wasted load. When the tab becomes visible again the
 * callback fires immediately (so the view is fresh on return) and polling
 * resumes.
 *
 * `callback` must be reference-stable (wrap it in `useCallback`): its identity
 * is an effect dependency, so a new identity restarts the timer with the
 * latest closure.
 */
export function useVisiblePolling(callback: () => void, intervalMs: number): void {
  useEffect(() => {
    let interval: ReturnType<typeof setInterval> | undefined;
    const stop = () => {
      if (interval !== undefined) {
        clearInterval(interval);
        interval = undefined;
      }
    };
    const start = () => {
      if (interval === undefined) {
        interval = setInterval(callback, intervalMs);
      }
    };
    const onVisibility = () => {
      if (document.hidden) {
        stop();
      } else {
        callback();
        start();
      }
    };

    // Initial run + start polling if the tab is currently visible.
    callback();
    if (!document.hidden) {
      start();
    }
    document.addEventListener('visibilitychange', onVisibility);
    return () => {
      stop();
      document.removeEventListener('visibilitychange', onVisibility);
    };
  }, [callback, intervalMs]);
}
