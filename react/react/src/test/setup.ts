import '@testing-library/jest-dom';
import { vi, beforeAll, afterAll } from 'vitest';

// Suppress console.warn and console.error during tests.
// This replaces per-file logger mocks. Remove these spies to debug test failures.
beforeAll(() => {
  vi.spyOn(console, 'warn').mockImplementation(() => {});
  vi.spyOn(console, 'error').mockImplementation(() => {});
});
afterAll(() => {
  vi.restoreAllMocks();
});

// Mock matchMedia for jsdom (used by react-hot-toast)
Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  }),
});
