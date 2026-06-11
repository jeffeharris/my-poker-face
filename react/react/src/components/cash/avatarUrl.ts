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

/** Build an absolutized avatar URL from a personality's display name, for
 *  surfaces that only have the name (e.g. a vouch world_event, which carries no
 *  avatar field). The backend `/api/avatar/<name>/<emotion>/full` route serves a
 *  fallback portrait if the emotion/persona is missing, and callers should still
 *  guard the <img> with onError. Defaults to a neutral emotion. */
export function avatarUrlForName(name: string, emotion = 'neutral'): string | null {
  if (!name) return null;
  return absolutizeAvatarUrl(`/api/avatar/${encodeURIComponent(name)}/${emotion}/full`);
}
