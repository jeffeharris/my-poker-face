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
        // Don't serve the SPA shell for API/WebSocket — or for the separate
        // static marketing site (landing, /opponents/*, /blog/*), which Caddy
        // routes to the poker-marketing container. Without these, the SW's
        // navigateFallback shadows those routes with the cached app index for
        // any visitor who has loaded the game (the SW is scoped to '/').
        navigateFallback: '/index.html',
        navigateFallbackDenylist: [
          /^\/api/,
          /^\/socket\.io/,
          /^\/$/, // homepage is the marketing landing
          /^\/opponents(\/|$)/,
          /^\/blog(\/|$)/,
        ],
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
  optimizeDeps: {
    // Pre-bundle the native-only Google-auth plugin on server start. It is only
    // ever pulled in via a guarded dynamic import (`src/native/googleSignIn.ts`,
    // reached eagerly through LoginForm), so vite discovers it LAZILY — and on a
    // fresh dev server a tab can hit that import before the dep is optimized,
    // surfacing a transient "[vite] Failed to resolve import" overlay until a
    // reload. Listing it here forces the pre-bundle up front so the dep is always
    // ready. (It ships a Capacitor web shim, so bundling it for web is a no-op at
    // runtime — the `isNativePlatform()` guard means web never actually calls it.)
    include: ['@codetrix-studio/capacitor-google-auth'],
  },
  server: {
    allowedHosts: ['homehub', 'frontend', 'macbook', '.ts.net'],
    // Proxy API and Socket.IO to backend when VITE_BACKEND_URL is set (Docker compose)
    ...(process.env.VITE_BACKEND_URL ? {
      proxy: {
        '/api': { target: process.env.VITE_BACKEND_URL, changeOrigin: true },
        '/socket.io': { target: process.env.VITE_BACKEND_URL, changeOrigin: true, ws: true },
        '/health': { target: process.env.VITE_BACKEND_URL, changeOrigin: true },
      },
    } : {}),
  },
})
