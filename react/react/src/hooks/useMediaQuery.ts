import { useState, useEffect } from 'react';

/**
 * Hook for matching custom CSS media queries.
 * For standard breakpoints (mobile/tablet/desktop), prefer useViewport() instead.
 *
 * @param query - CSS media query string, e.g., '(min-width: 1024px)'
 * @returns boolean indicating if the query matches
 */
export function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(() => {
    if (typeof window !== 'undefined') {
      return window.matchMedia(query).matches;
    }
    return false;
  });

  useEffect(() => {
    const mediaQuery = window.matchMedia(query);
    const handler = (e: MediaQueryListEvent) => setMatches(e.matches);

    mediaQuery.addEventListener('change', handler);
    return () => mediaQuery.removeEventListener('change', handler);
  }, [query]);

  return matches;
}
