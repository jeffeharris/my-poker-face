import { defineConfig, devices } from '@playwright/test';

const baseURL = process.env.BASE_URL || 'http://localhost:5173';

export default defineConfig({
  testDir: './e2e',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: 0,
  // Safari runs serially in CI (fullyParallel: false), so workers only affects Chrome
  workers: process.env.CI ? 4 : 8,
  reporter: 'list',
  use: {
    baseURL,
    trace: 'on-first-retry',
    reducedMotion: 'reduce',
  },
  // Skip webServer when BASE_URL is externally provided (e.g. Docker compose)
  ...(process.env.BASE_URL
    ? {}
    : {
        webServer: {
          command: 'npm run dev',
          url: 'http://localhost:5173',
          reuseExistingServer: !process.env.CI,
          timeout: 30000,
        },
      }),
  projects: [
    {
      name: 'Mobile Safari',
      use: {
        ...devices['iPhone 13'],
      },
      // WebKit is more sensitive to timing under parallel load; run serially in CI
      fullyParallel: !process.env.CI,
      retries: process.env.CI ? 1 : 0,
    },
    {
      name: 'Mobile Chrome',
      use: {
        ...devices['Pixel 5'],
      },
    },
  ],
});
