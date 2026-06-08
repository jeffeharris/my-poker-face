import { io, type Socket, type ManagerOptions, type SocketOptions } from 'socket.io-client';
import { getAccessToken } from './nativeAuth';

/**
 * Create a Socket.IO connection with auth wired for both transports.
 *
 * Browsers authenticate the handshake via the session cookie (`withCredentials`).
 * Native (Capacitor) clients can't set headers on the WebSocket upgrade, so we
 * pass the access token in the Socket.IO `auth` payload, which the backend's
 * `connect` handler reads (see `AuthManager.authenticate_socket`). On the web
 * there's no token, so `auth` is omitted and nothing changes.
 */
export function createAuthedSocket(
  url: string,
  opts: Partial<ManagerOptions & SocketOptions> = {}
): Socket {
  const token = getAccessToken();
  return io(url, {
    ...opts,
    ...(token ? { auth: { token } } : {}),
  });
}
