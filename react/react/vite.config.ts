import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { VitePWA } from 'vite-plugin-pwa'

// https://vite.dev/config/
export default defineConfig({
  plugins: [
    react(),
    VitePWA({
      registerType: 'autoUpdate',
      includeAssets: ['poker-favicon.svg', 'apple-touch-icon-180x180.png'],
      manifest: {
        name: 'My Poker Face',
        short_name: 'Poker Face',
        description: 'Play poker against AI personalities with unique playing styles',
        start_url: '/',
        display: 'standalone',
        theme_color: '#dc2626',
        background_color: '#1a202c',
        icons: [
          {
            src: '/icon-192x192.png',
            sizes: '192x192',
            type: 'image/png',
          },
          {
            src: '/icon-512x512.png',
            sizes: '512x512',
            type: 'image/png',
          },
          {
            src: '/icon-512x512.png',
            sizes: '512x512',
            type: 'image/png',
            purpose: 'maskable',
          },
        ],
      },
      workbox: {
        // Cache static assets
        globPatterns: ['**/*.{js,css,html,ico,png,svg,woff,woff2}'],
        // Don't cache API calls or WebSocket
        navigateFallback: '/index.html',
        navigateFallbackDenylist: [/^\/api/, /^\/socket.io/],
        runtimeCaching: [
          {
            urlPattern: /^https:\/\/fonts\.googleapis\.com\/.*/i,
            handler: 'CacheFirst',
            options: {
              cacheName: 'google-fonts-cache',
              expiration: {
                maxEntries: 10,
                maxAgeSeconds: 60 * 60 * 24 * 365, // 1 year
              },
            },
          },
        ],
      },
      devOptions: {
        enabled: false, // Disabled - was causing caching issues during dev
        type: 'module',
      },
    }),
  ],
  server: {
    allowedHosts: ['homehub'],
  },
})
