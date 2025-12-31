import { useEffect, useRef, useCallback } from 'react';

interface UsePollingOptions {
  enabled?: boolean;
  immediate?: boolean;
}

export function usePolling(
  callback: () => void | Promise<void>,
  interval: number,
  options: UsePollingOptions = {}
) {
  const { enabled = true, immediate = false } = options;
  const savedCallback = useRef(callback);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Update callback ref when it changes
  useEffect(() => {
    savedCallback.current = callback;
  }, [callback]);

  const start = useCallback(() => {
    if (intervalRef.current) return; // Already running
    
    const tick = async () => {
      await savedCallback.current();
    };

    // Execute immediately if requested
    if (immediate) {
      tick();
    }

    intervalRef.current = setInterval(tick, interval);
  }, [interval, immediate]);

  const stop = useCallback(() => {
    if (intervalRef.current) {
      clearInterval(intervalRef.current);
      intervalRef.current = null;
    }
  }, []);

  useEffect(() => {
    if (enabled) {
      start();
    } else {
      stop();
    }

    return stop;
  }, [enabled, start, stop]);

  return { start, stop };
}