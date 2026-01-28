import { config } from '../config';

export const logger = {
  debug(...args: unknown[]): void {
    if (config.ENABLE_DEBUG) console.debug(...args);
  },
  log(...args: unknown[]): void {
    if (config.ENABLE_DEBUG) console.log(...args);
  },
  warn(...args: unknown[]): void {
    console.warn(...args);
  },
  error(...args: unknown[]): void {
    console.error(...args);
  },
};
