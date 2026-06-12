import { useEffect, useRef } from 'react';
import { Socket } from 'socket.io-client';
import { config } from '../config';
import { createAuthedSocket } from '../utils/socket';

interface UseSocketOptions {
  onConnect?: () => void;
  onDisconnect?: () => void;
  autoConnect?: boolean;
}

// In dev, the Flask backend runs under Werkzeug with
// `async_mode='threading'`. That combo doesn't speak WebSocket
// cleanly — long-polling works fine, but the polling→WS upgrade
// probe sometimes fires with malformed frames ("Invalid frame header"
// in the browser console). Pinning transport to 'polling' in dev
// avoids the failed upgrade attempts entirely. Production runs
// gunicorn + GeventWebSocketWorker behind Caddy, which handles WS
// properly — there we let socket.io negotiate transports normally.
const SOCKET_TRANSPORTS = import.meta.env.PROD ? undefined : ['polling'];

export function useSocket(url: string = config.SOCKET_URL, options: UseSocketOptions = {}) {
  const socketRef = useRef<Socket | null>(null);

  useEffect(() => {
    if (options.autoConnect !== false) {
      const socket = createAuthedSocket(url, {
        withCredentials: true,
        ...(SOCKET_TRANSPORTS ? { transports: SOCKET_TRANSPORTS } : {}),
      });
      socketRef.current = socket;

      if (options.onConnect) {
        socket.on('connect', options.onConnect);
      }

      if (options.onDisconnect) {
        socket.on('disconnect', options.onDisconnect);
      }

      return () => {
        socket.disconnect();
      };
    }
    // Intentionally omit callbacks - we don't want to reconnect when callbacks change
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [url, options.autoConnect]);

  const connect = () => {
    if (!socketRef.current || !socketRef.current.connected) {
      socketRef.current = createAuthedSocket(url, {
        withCredentials: true,
        ...(SOCKET_TRANSPORTS ? { transports: SOCKET_TRANSPORTS } : {}),
      });
    }
  };

  const disconnect = () => {
    if (socketRef.current) {
      socketRef.current.disconnect();
    }
  };

  return {
    socket: socketRef.current,
    connect,
    disconnect,
  };
}
