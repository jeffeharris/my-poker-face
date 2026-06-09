// @ts-check
import { defineConfig } from 'astro/config';
import react from '@astrojs/react';
import sitemap from '@astrojs/sitemap';

// The marketing site is fully static (SSG). Every page is prerendered to plain
// HTML at build time — that is the whole point: maximum crawlability, zero JS
// dependency for content. React is used only for interactive "islands" (the
// suggest-a-character form). The game SPA is a separate app.
export default defineConfig({
  site: 'https://mypokerfacegame.com',
  // Clean URLs: /opponents/sherlock-holmes/ -> .../index.html
  build: { format: 'directory' },
  // Bind dev + preview to 0.0.0.0 so the site is reachable on the LAN/containers.
  server: { host: true },
  // Vite blocks requests whose Host header isn't allowlisted — needed to reach
  // the dev/preview server by hostname (e.g. http://homehub:4321) rather than IP.
  vite: {
    server: { allowedHosts: ['homehub', 'frontend'] },
    preview: { allowedHosts: ['homehub', 'frontend'] },
  },
  integrations: [react(), sitemap()],
});
