import { io, type Socket, type ManagerOptions, type SocketOptions } from 'socket.io-client';
import { getAccessToken, refreshAccessToken, hasNativeSession } from './nativeAuth';

/**
 * Create a Socket.IO connection with auth wired for both transports.
 *
 * Browsers authenticate the handshake via the session cookie (`withCredentials`).
 * Native (Capacitor) clients can't set headers on the WebSocket upgrade, so we
 * pass the access token in the Socket.IO `auth` payload, which the backend's
 * `connect` handler reads (see `AuthManager.authenticate_socket`). On the web
 * there's no token, so `auth` resolves to empty and nothing changes.
 *
 * IMPORTANT: `auth` is a *callback*, not a static object. Socket.IO invokes it
 * before every (re)connection attempt, so each reconnect reads the CURRENT
 * token. A static `{ token }` would freeze the value captured at creation — and
 * since native access tokens are short-lived, once it expired (e.g. while the
 * app was backgrounded) every automatic reconnect would re-handshake with a
 * dead token and loop forever, leaving the game state silently stale.
 */
export function createAuthedSocket(
  url: string,
  opts: Partial<ManagerOptions & SocketOptions> = {}
): Socket {
  const socket = io(url, {
    ...opts,
    auth: (cb) => {
      const token = getAccessToken();
      cb(token ? { token } : {});
    },
  });

  // If the handshake fails on native, the most likely cause is an expired access
  // token. Refresh once, then nudge the reconnect so it re-handshakes with the
  // fresh token (the `auth` callback above picks it up). Web auths via cookie and
  // holds no native session, so this is a no-op there.
  socket.on('connect_error', () => {
    if (!hasNativeSession()) return;
    void refreshAccessToken().then((refreshed) => {
      if (refreshed && !socket.connected) {
        socket.connect();
      }
    });
  });

  return socket;
}
