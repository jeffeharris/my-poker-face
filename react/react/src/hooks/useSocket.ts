import { useEffect, useRef } from 'react';
import { io, Socket } from 'socket.io-client';
import { config } from '../config';

interface UseSocketOptions {
  onConnect?: () => void;
  onDisconnect?: () => void;
  autoConnect?: boolean;
}

export function useSocket(url: string = config.SOCKET_URL, options: UseSocketOptions = {}) {
  const socketRef = useRef<Socket | null>(null);

  useEffect(() => {
    if (options.autoConnect !== false) {
      const socket = io(url, { withCredentials: true });
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
  }, [url, options.autoConnect]);

  const connect = () => {
    if (!socketRef.current || !socketRef.current.connected) {
      socketRef.current = io(url, { withCredentials: true });
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