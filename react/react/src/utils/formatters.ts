/**
 * Shared formatting utilities for consistent display across components.
 */

/**
 * Format a date string for display.
 * Returns date and time in locale format.
 */
export function formatDate(dateStr: string | null): string {
  if (!dateStr) return '-';
  const date = new Date(dateStr);
  return date.toLocaleDateString() + ' ' + date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

/**
 * Format milliseconds to human-readable latency string.
 * Shows seconds for values >= 1000ms, otherwise shows ms.
 */
export function formatLatency(ms: number): string {
  if (ms >= 1000) return `${(ms / 1000).toFixed(1)}s`;
  return `${Math.round(ms)}ms`;
}

/**
 * Format cost value with appropriate precision.
 * Adapts decimal places based on magnitude for readability.
 */
export function formatCost(cost: number): string {
  if (cost === 0) return '$0';
  if (cost < 0.0001) return `$${cost.toExponential(1)}`;
  if (cost < 0.001) return `$${cost.toFixed(6)}`;
  if (cost < 0.01) return `$${cost.toFixed(4)}`;
  if (cost < 1) return `$${cost.toFixed(3)}`;
  return `$${cost.toFixed(2)}`;
}

/**
 * Format a number with thousands separators.
 */
export function formatNumber(num: number): string {
  return num.toLocaleString();
}

/**
 * Format percentage with specified decimal places.
 */
export function formatPercent(value: number, decimals: number = 1): string {
  return `${value.toFixed(decimals)}%`;
}

/**
 * Truncate a string to max length with ellipsis.
 */
export function truncate(str: string, maxLen: number = 20): string {
  if (str.length <= maxLen) return str;
  return str.slice(0, maxLen - 2) + '..';
}
