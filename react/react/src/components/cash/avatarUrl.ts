import { config } from '../../config';

/** Avatar URLs from the cash routes are returned as relative paths
 *  ("/api/avatar/<name>/<emotion>/full"). In dev, the frontend runs
 *  on a different port from the backend and Vite's proxy isn't
 *  configured (the env var that enables it isn't set), so relative
 *  paths hit the Vite SPA fallback and return index.html instead of
 *  the image. Prefix with config.API_URL to send the request straight
 *  to the backend. In production (PROD build), config.API_URL is ''
 *  so this is a no-op. */
export function absolutizeAvatarUrl(url: string | null): string | null {
  if (!url) return null;
  if (url.startsWith('http://') || url.startsWith('https://')) return url;
  return `${config.API_URL}${url}`;
}
