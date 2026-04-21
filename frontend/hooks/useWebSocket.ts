"use client";

import { useEffect, useRef, useState, useCallback } from "react";

// ---------------------------------------------------------------------------
// Channel types
// ---------------------------------------------------------------------------

export type WSChannel =
  | "market"
  | "signals"
  | "trades"
  | "decisions"
  | "positions"
  | "alerts"
  | "engine"
  | "macro";

// ---------------------------------------------------------------------------
// Message types
// ---------------------------------------------------------------------------

export interface WSMessage {
  type: string;
  ts?: string;
  [key: string]: any;
}

export interface PositionUpdateMessage extends WSMessage {
  type: "position_update";
  position_id: string;
  symbol: string;
  profile: "spot" | "futures";
  unrealised_pnl: number;
  mark_price: number;
  liq_distance_pct?: number;
}

export interface AlertMessage extends WSMessage {
  type:
    | "TP_HIT"
    | "SL_HIT"
    | "EMERGENCY"
    | "ANTI_LIQ_WARNING"
    | "FUNDING_DRAIN"
    | "LIQUIDATED";
  symbol: string;
  profile: "spot" | "futures";
  details: Record<string, any>;
}

export interface EngineStatusMessage extends WSMessage {
  type: "engine_status";
  profile: "spot" | "futures";
  running: boolean;
  paused: boolean;
  cycle: number;
  last_scan?: string;
}

export interface MacroUpdateMessage extends WSMessage {
  type: "macro_update";
  regime:
    | "STRONG_RISK_ON"
    | "RISK_ON"
    | "NEUTRAL"
    | "RISK_OFF"
    | "STRONG_RISK_OFF";
  score: number;
  components: Record<string, number>;
}

export interface PriceUpdateMessage extends WSMessage {
  type: "price_update";
  symbol: string;
  price: number;
  change_24h: number;
  score: number;
}

export interface DecisionItem {
  id: number;
  symbol: string;
  strategy: string;
  timeframe?: string | null;
  score?: number | null;
  decision: "ALLOW" | "BLOCK";
  l1_pass?: boolean | null;
  l2_pass?: boolean | null;
  l3_pass?: boolean | null;
  reasons?: Record<string, unknown>;
  metrics?: Record<string, unknown>;
  latency_ms?: number | null;
  created_at: string;
}

export interface DecisionCreatedMessage extends WSMessage {
  type: "decision.created";
  data: DecisionItem;
}

export type AlertSeverity = "info" | "warning" | "critical" | "emergency";

export function getAlertSeverity(type: AlertMessage["type"]): AlertSeverity {
  switch (type) {
    case "TP_HIT":
    case "SL_HIT":
      return "info";
    case "ANTI_LIQ_WARNING":
    case "FUNDING_DRAIN":
      return "warning";
    case "EMERGENCY":
    case "LIQUIDATED":
      return "emergency";
    default:
      return "info";
  }
}

// ---------------------------------------------------------------------------
// Utility
// ---------------------------------------------------------------------------

export function getCurrentUserId(): string | undefined {
  try {
    const user = localStorage.getItem("user");
    return user ? JSON.parse(user).id : undefined;
  } catch {
    return undefined;
  }
}

function getWsUrl(channel: WSChannel): string {
  if (typeof window === "undefined") return "";
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  const host = process.env.NEXT_PUBLIC_API_URL
    ? new URL(process.env.NEXT_PUBLIC_API_URL).host
    : window.location.host;
  return `${proto}//${host}/ws/${channel}`;
}

// ---------------------------------------------------------------------------
// Base hook
// ---------------------------------------------------------------------------

interface UseWebSocketResult<T extends WSMessage = WSMessage> {
  data: T | null;
  lastMessage: T | null;
  isConnected: boolean;
  error: string | null;
  send: (msg: string | object) => void;
  reconnect: () => void;
}

const MAX_RETRIES = 10;
const PING_INTERVAL_MS = 20_000;

export function useWebSocket<T extends WSMessage = WSMessage>(
  channel: WSChannel,
  userId?: string
): UseWebSocketResult<T> {
  const [data, setData] = useState<T | null>(null);
  const [isConnected, setIsConnected] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const wsRef = useRef<WebSocket | null>(null);
  const retriesRef = useRef(0);
  const unmountedRef = useRef(false);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const connect = useCallback(() => {
    const url = getWsUrl(channel);
    if (!url || unmountedRef.current) return;

    try {
      const ws = new WebSocket(url);
      wsRef.current = ws;

      ws.onopen = () => {
        if (unmountedRef.current) return;
        setIsConnected(true);
        setError(null);
        retriesRef.current = 0;

        if (userId) {
          ws.send(JSON.stringify({ user_id: userId }));
        }
      };

      ws.onmessage = (event: MessageEvent) => {
        if (unmountedRef.current) return;
        try {
          const parsed: T = JSON.parse(event.data as string);
          setData(parsed);
        } catch {
          // Non-JSON frames (e.g. "pong") are silently ignored
        }
      };

      ws.onerror = () => {
        if (unmountedRef.current) return;
        setError("WebSocket error");
      };

      ws.onclose = () => {
        if (unmountedRef.current) return;
        setIsConnected(false);
        wsRef.current = null;

        if (retriesRef.current < MAX_RETRIES) {
          const delay = Math.min(1_000 * 2 ** retriesRef.current, 30_000);
          retriesRef.current += 1;
          reconnectTimerRef.current = setTimeout(connect, delay);
        } else {
          setError("Max reconnect attempts reached");
        }
      };
    } catch {
      setError("Failed to connect");
    }
  }, [channel, userId]); // eslint-disable-line react-hooks/exhaustive-deps

  // Manual reconnect: reset retry counter then open a fresh socket
  const reconnect = useCallback(() => {
    if (reconnectTimerRef.current) {
      clearTimeout(reconnectTimerRef.current);
      reconnectTimerRef.current = null;
    }
    wsRef.current?.close();
    retriesRef.current = 0;
    connect();
  }, [connect]);

  // Lifecycle
  useEffect(() => {
    unmountedRef.current = false;
    connect();

    return () => {
      unmountedRef.current = true;
      if (reconnectTimerRef.current) clearTimeout(reconnectTimerRef.current);
      wsRef.current?.close();
    };
  }, [connect]);

  // Ping keepalive
  useEffect(() => {
    const id = setInterval(() => {
      if (wsRef.current?.readyState === WebSocket.OPEN) {
        wsRef.current.send("ping");
      }
    }, PING_INTERVAL_MS);
    return () => clearInterval(id);
  }, []);

  const send = useCallback((msg: string | object) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(
        typeof msg === "string" ? msg : JSON.stringify(msg)
      );
    }
  }, []);

  return { data, lastMessage: data, isConnected, error, send, reconnect };
}

// ---------------------------------------------------------------------------
// Specialized hooks
// ---------------------------------------------------------------------------

/** Maintains a Map<position_id, latest PositionUpdateMessage>. */
export function usePositionsWS(userId?: string): {
  positionUpdates: Map<string, PositionUpdateMessage>;
  isConnected: boolean;
} {
  const { lastMessage, isConnected } =
    useWebSocket<PositionUpdateMessage>("positions", userId);

  const [positionUpdates, setPositionUpdates] = useState<
    Map<string, PositionUpdateMessage>
  >(new Map());

  useEffect(() => {
    if (lastMessage?.type === "position_update" && lastMessage.position_id) {
      setPositionUpdates((prev) => {
        const next = new Map(prev);
        next.set(lastMessage.position_id, lastMessage);
        return next;
      });
    }
  }, [lastMessage]);

  return { positionUpdates, isConnected };
}

const ALERT_BUFFER_SIZE = 20;

/** Keeps a rolling buffer of the last 20 alerts and fires a callback per alert. */
export function useAlertsWS(
  userId?: string,
  onAlert?: (alert: AlertMessage) => void
): {
  alerts: AlertMessage[];
  isConnected: boolean;
  clearAlerts: () => void;
} {
  const { lastMessage, isConnected } =
    useWebSocket<AlertMessage>("alerts", userId);

  const [alerts, setAlerts] = useState<AlertMessage[]>([]);
  const onAlertRef = useRef(onAlert);
  onAlertRef.current = onAlert;

  useEffect(() => {
    if (!lastMessage?.type) return;
    const alertTypes: AlertMessage["type"][] = [
      "TP_HIT",
      "SL_HIT",
      "EMERGENCY",
      "ANTI_LIQ_WARNING",
      "FUNDING_DRAIN",
      "LIQUIDATED",
    ];
    if (!alertTypes.includes(lastMessage.type as AlertMessage["type"])) return;

    const alert = lastMessage as AlertMessage;
    setAlerts((prev) =>
      [alert, ...prev].slice(0, ALERT_BUFFER_SIZE)
    );
    onAlertRef.current?.(alert);
  }, [lastMessage]);

  const clearAlerts = useCallback(() => setAlerts([]), []);

  return { alerts, isConnected, clearAlerts };
}

/** Tracks the latest engine status for a given profile. */
export function useEngineStatusWS(
  profile: "spot" | "futures",
  userId?: string
): {
  status: EngineStatusMessage | null;
  isConnected: boolean;
} {
  const { lastMessage, isConnected } =
    useWebSocket<EngineStatusMessage>("engine", userId);

  const [status, setStatus] = useState<EngineStatusMessage | null>(null);

  useEffect(() => {
    if (
      lastMessage?.type === "engine_status" &&
      lastMessage.profile === profile
    ) {
      setStatus(lastMessage);
    }
  }, [lastMessage, profile]);

  return { status, isConnected };
}

/** Tracks the current macro regime. */
export function useMacroWS(): {
  regime: string | null;
  score: number | null;
  lastUpdate: string | null;
  isConnected: boolean;
} {
  const { lastMessage, isConnected } =
    useWebSocket<MacroUpdateMessage>("macro");

  const [regime, setRegime] = useState<string | null>(null);
  const [score, setScore] = useState<number | null>(null);
  const [lastUpdate, setLastUpdate] = useState<string | null>(null);

  useEffect(() => {
    if (lastMessage?.type === "macro_update") {
      setRegime(lastMessage.regime);
      setScore(lastMessage.score);
      setLastUpdate(lastMessage.ts ?? new Date().toISOString());
    }
  }, [lastMessage]);

  return { regime, score, lastUpdate, isConnected };
}

/** Maintains a Map<symbol, latest PriceUpdateMessage> from the market channel. */
export function useMarketWS(): {
  prices: Map<string, PriceUpdateMessage>;
  isConnected: boolean;
} {
  const { lastMessage, isConnected } =
    useWebSocket<PriceUpdateMessage>("market");

  const [prices, setPrices] = useState<Map<string, PriceUpdateMessage>>(
    new Map()
  );

  useEffect(() => {
    if (lastMessage?.type === "price_update" && lastMessage.symbol) {
      setPrices((prev) => {
        const next = new Map(prev);
        next.set(lastMessage.symbol, lastMessage);
        return next;
      });
    }
  }, [lastMessage]);

  return { prices, isConnected };
}
