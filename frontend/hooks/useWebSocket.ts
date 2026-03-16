"use client";

import { useEffect, useRef, useState, useCallback } from "react";

interface UseWebSocketResult {
  data: any;
  isConnected: boolean;
  error: string | null;
  send: (msg: string) => void;
}

/**
 * WebSocket hook with auto-reconnect and exponential backoff.
 *
 * Usage:
 *   const { data, isConnected } = useWebSocket("market");
 */
export function useWebSocket(channel: string): UseWebSocketResult {
  const [data, setData] = useState<any>(null);
  const [isConnected, setIsConnected] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const retriesRef = useRef(0);
  const maxRetries = 10;

  const getWsUrl = useCallback(() => {
    if (typeof window === "undefined") return "";
    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    const host = process.env.NEXT_PUBLIC_API_URL
      ? new URL(process.env.NEXT_PUBLIC_API_URL).host
      : window.location.host;
    return `${proto}//${host}/ws/${channel}`;
  }, [channel]);

  const connect = useCallback(() => {
    const url = getWsUrl();
    if (!url) return;

    try {
      const ws = new WebSocket(url);
      wsRef.current = ws;

      ws.onopen = () => {
        setIsConnected(true);
        setError(null);
        retriesRef.current = 0;
      };

      ws.onmessage = (event) => {
        try {
          const parsed = JSON.parse(event.data);
          setData(parsed);
        } catch {
          setData(event.data);
        }
      };

      ws.onerror = () => {
        setError("WebSocket error");
      };

      ws.onclose = () => {
        setIsConnected(false);
        wsRef.current = null;

        // Auto-reconnect with exponential backoff
        if (retriesRef.current < maxRetries) {
          const delay = Math.min(1000 * 2 ** retriesRef.current, 30000);
          retriesRef.current += 1;
          setTimeout(connect, delay);
        }
      };
    } catch (e) {
      setError("Failed to connect");
    }
  }, [getWsUrl]);

  useEffect(() => {
    connect();
    return () => {
      retriesRef.current = maxRetries; // Prevent reconnect on unmount
      wsRef.current?.close();
    };
  }, [connect]);

  // Ping keepalive
  useEffect(() => {
    const interval = setInterval(() => {
      if (wsRef.current?.readyState === WebSocket.OPEN) {
        wsRef.current.send("ping");
      }
    }, 30000);
    return () => clearInterval(interval);
  }, []);

  const send = useCallback((msg: string) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(msg);
    }
  }, []);

  return { data, isConnected, error, send };
}
