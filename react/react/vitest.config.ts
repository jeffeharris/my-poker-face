import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    include: ['src/**/*.test.{ts,tsx}'],
    env: {
      VITE_API_URL: 'http://localhost:5000',
      VITE_SOCKET_URL: 'http://localhost:5000',
      VITE_ENABLE_DEBUG: 'false',
      VITE_ENABLE_AI_DEBUG: 'false',
    },
  },
});
