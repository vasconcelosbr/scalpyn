"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

/**
 * Decoded payload of a single ``trade_decisions`` row, as emitted by
 * the backend ``decision_event_bus`` and consumed via SSE.
 *
 * Shape mirrors the JSON dict built in
 * ``decision_audit_service._record_decision_raw``. All optional
 * fields are nullable in DB and therefore optional here too.
 */
export interface LiveDecisionEvent {
  trace_id: string;
  user_id?: string | null;
  pool_id?: string | null;
  symbol: string;
  market_type: "spot" | "futures" | string;
  exchange?: string | null;
  status: "APPROVED" | "REJECTED" | "SKIPPED" | "BLOCKED" | string;
  stage?: string | null;
  reason?: string | null;
  blocking_rule?: string | null;
  rule_details?: Record<string, unknown> | null;
  score_breakdown?: Record<string, unknown> | null;
  latency_ms?: Record<string, number> | null;
  trade_id?: string | null;
  decided_at?: string;
}

export type LogFilter = "all" | "spot" | "futures" | "APPROVED" | "REJECTED";

export type StreamStatus = "connecting" | "live" | "offline";

export interface UseLiveLogStreamOptions {
  /** Hard cap on the in-memory buffer (oldest events evicted). */
  maxBuffer?: number;
  /** Reconnect base delay in ms (capped exponential). */
  reconnectDelayMs?: number;
}

export interface UseLiveLogStreamReturn {
  /** Buffered + filtered + (when paused) frozen events list. */
  events: LiveDecisionEvent[];
  /** Total events received this session, ignoring filters/pause. */
  totalReceived: number;
  /** Raw APPROVED count over the in-memory buffer (NOT filtered by chip / pause). */
  rawApprovedCount: number;
  /** Raw REJECTED count over the in-memory buffer (NOT filtered by chip / pause). */
  rawRejectedCount: number;
  /** Current connection status. */
  status: StreamStatus;
  /** Active filter (clicked chip). */
  filter: LogFilter;
  setFilter: (f: LogFilter) => void;
  /** Pause the visible buffer (SSE stays connected). */
  paused: boolean;
  setPaused: (p: boolean) => void;
  /** Drop everything from the local buffer (does not disconnect). */
  clear: () => void;
}

const DEFAULT_MAX = 200;
const DEFAULT_RECONNECT_MS = 5_000;
const SSE_URL = "/api/live/log-stream";


/**
 * SSE consumer for the diagnostics page.
 *
 * EventSource doesn't allow custom headers, so we can't use the JWT
 * via ``Authorization: Bearer …`` with the native API. We hand-roll
 * the SSE protocol over ``fetch`` + a streamed ``ReadableStream`` so
 * the existing localStorage-token auth flow keeps working without
 * having to expose a token via query string (which would leak into
 * server access logs).
 *
 * Reconnect logic:
 *   * On any error / EOF / non-2xx response, status flips to
 *     ``"offline"`` and we wait ``reconnectDelayMs`` before retrying.
 *   * The retry loop runs until the component unmounts (AbortController
 *     drives shutdown).
 *   * Heartbeat comments (``: heartbeat``) sent by the server keep the
 *     stream alive but produce no event — they're skipped by the
 *     parser below since they don't start with ``data:``.
 *
 * Buffer management:
 *   * We push to a ref (``bufferRef``) and copy to React state on a
 *     micro-task to coalesce rapid bursts (avoids re-rendering once
 *     per event when the pipeline produces many decisions in a tick).
 *   * When ``paused`` is true, buffer keeps growing in the background
 *     up to ``maxBuffer``; the *visible* events array is the snapshot
 *     taken when ``paused`` was toggled on.
 */
export function useLiveLogStream(
  opts: UseLiveLogStreamOptions = {}
): UseLiveLogStreamReturn {
  const maxBuffer = opts.maxBuffer ?? DEFAULT_MAX;
  const reconnectDelayMs = opts.reconnectDelayMs ?? DEFAULT_RECONNECT_MS;

  const [events, setEvents] = useState<LiveDecisionEvent[]>([]);
  const [status, setStatus] = useState<StreamStatus>("connecting");
  const [filter, setFilter] = useState<LogFilter>("all");
  const [paused, setPausedState] = useState(false);

  // Counters used by the cycle-metrics card. Kept in refs so a burst
  // of N events causes a single coalesced React render (set inside
  // ``scheduleFlush``) instead of N renders — this is the "render
  // storm" pitfall the previous implementation had.
  const totalReceivedRef = useRef(0);
  const approvedRawRef = useRef(0);
  const rejectedRawRef = useRef(0);
  const [totalReceived, setTotalReceived] = useState(0);
  const [rawApprovedCount, setRawApprovedCount] = useState(0);
  const [rawRejectedCount, setRawRejectedCount] = useState(0);

  const bufferRef = useRef<LiveDecisionEvent[]>([]);
  const pausedRef = useRef(false);
  const frozenRef = useRef<LiveDecisionEvent[] | null>(null);
  const flushPendingRef = useRef(false);

  // Schedule a coalesced state update so a burst of N events triggers
  // a single React render instead of N. We flush both the visible
  // buffer AND the running counters in the same micro-task so the
  // BalanceMetrics card stays in sync with the trace viewer.
  const scheduleFlush = useCallback(() => {
    if (flushPendingRef.current) return;
    flushPendingRef.current = true;
    queueMicrotask(() => {
      flushPendingRef.current = false;
      // Counters always update — they're independent of pause state.
      setTotalReceived(totalReceivedRef.current);
      setRawApprovedCount(approvedRawRef.current);
      setRawRejectedCount(rejectedRawRef.current);
      if (pausedRef.current && frozenRef.current !== null) {
        // Visible buffer is frozen; ignore the events flush until unpause.
        return;
      }
      setEvents([...bufferRef.current]);
    });
  }, []);

  const setPaused = useCallback((p: boolean) => {
    pausedRef.current = p;
    if (p) {
      // Snapshot the current buffer so the table stops moving.
      frozenRef.current = [...bufferRef.current];
      setEvents(frozenRef.current);
    } else {
      frozenRef.current = null;
      setEvents([...bufferRef.current]);
    }
    setPausedState(p);
  }, []);

  const clear = useCallback(() => {
    bufferRef.current = [];
    frozenRef.current = pausedRef.current ? [] : null;
    // Counters reflect "events currently in buffer" — clearing the
    // buffer must also clear them. ``totalReceived`` keeps its session
    // tally so the operator can still see throughput.
    approvedRawRef.current = 0;
    rejectedRawRef.current = 0;
    setRawApprovedCount(0);
    setRawRejectedCount(0);
    setEvents([]);
  }, []);

  const pushEvent = useCallback(
    (evt: LiveDecisionEvent) => {
      const buf = bufferRef.current;
      buf.push(evt);
      if (evt.status === "APPROVED") approvedRawRef.current++;
      else if (evt.status === "REJECTED") rejectedRawRef.current++;
      if (buf.length > maxBuffer) {
        // Evict oldest; decrement the matching counter so the running
        // raw counts always reflect the current buffer contents (not
        // a session-wide tally — see ``totalReceivedRef`` for that).
        const evicted = buf.splice(0, buf.length - maxBuffer);
        for (const e of evicted) {
          if (e.status === "APPROVED" && approvedRawRef.current > 0) approvedRawRef.current--;
          else if (e.status === "REJECTED" && rejectedRawRef.current > 0) rejectedRawRef.current--;
        }
      }
      totalReceivedRef.current++;
      scheduleFlush();
    },
    [maxBuffer, scheduleFlush]
  );

  // ── Connection loop ────────────────────────────────────────────────
  useEffect(() => {
    const ac = new AbortController();
    let cancelled = false;
    let retryTimer: ReturnType<typeof setTimeout> | null = null;

    async function readStream() {
      while (!cancelled) {
        setStatus("connecting");

        const token =
          typeof window === "undefined"
            ? null
            : window.localStorage.getItem("token");
        const headers: Record<string, string> = { Accept: "text/event-stream" };
        if (token) headers.Authorization = `Bearer ${token}`;

        try {
          const res = await fetch(SSE_URL, {
            method: "GET",
            headers,
            signal: ac.signal,
            cache: "no-store",
          });

          if (!res.ok || !res.body) {
            if (!cancelled) setStatus("offline");
            await wait(reconnectDelayMs, ac.signal);
            continue;
          }

          if (!cancelled) setStatus("live");

          const reader = res.body
            .pipeThrough(new TextDecoderStream())
            .getReader();
          let pending = "";

          while (!cancelled) {
            const { value, done } = await reader.read();
            if (done) break;
            pending += value;

            // SSE messages end with a blank line (`\n\n`). Each message
            // can have multiple ``data:`` lines that should be joined
            // by ``\n`` per the spec; we use ``data:`` only here.
            let sepIdx;
            while ((sepIdx = pending.indexOf("\n\n")) !== -1) {
              const raw = pending.slice(0, sepIdx);
              pending = pending.slice(sepIdx + 2);

              const dataLines: string[] = [];
              for (const line of raw.split("\n")) {
                if (line.startsWith("data:")) {
                  dataLines.push(line.slice(5).trimStart());
                }
              }
              if (dataLines.length === 0) continue; // heartbeat or comment
              const dataStr = dataLines.join("\n");
              try {
                const parsed = JSON.parse(dataStr) as LiveDecisionEvent;
                if (parsed && typeof parsed === "object") {
                  pushEvent(parsed);
                }
              } catch {
                // Malformed payload — drop silently.
              }
            }
          }

          if (!cancelled) setStatus("offline");
          await wait(reconnectDelayMs, ac.signal);
        } catch (err) {
          if (cancelled || (err as Error)?.name === "AbortError") return;
          setStatus("offline");
          await wait(reconnectDelayMs, ac.signal);
        }
      }
    }

    readStream();

    return () => {
      cancelled = true;
      if (retryTimer) clearTimeout(retryTimer);
      ac.abort();
    };
  }, [reconnectDelayMs, pushEvent]);

  // ── Filtering ──────────────────────────────────────────────────────
  const filtered = useMemo(() => {
    if (filter === "all") return events;
    if (filter === "spot" || filter === "futures") {
      return events.filter((e) => e.market_type === filter);
    }
    return events.filter((e) => e.status === filter);
  }, [events, filter]);

  return {
    events: filtered,
    totalReceived,
    rawApprovedCount,
    rawRejectedCount,
    status,
    filter,
    setFilter,
    paused,
    setPaused,
    clear,
  };
}

/**
 * ``setTimeout`` with abort support that **always** removes its listener
 * on resolve. Earlier versions registered an ``abort`` listener with
 * ``{once: true}`` but never cleared it on the normal-resolve path,
 * so a long reconnect loop would accumulate listeners on the same
 * AbortSignal until the component unmounted.
 */
function wait(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    if (signal.aborted) {
      resolve();
      return;
    }
    let t: ReturnType<typeof setTimeout> | null = null;
    const onAbort = () => {
      if (t !== null) clearTimeout(t);
      signal.removeEventListener("abort", onAbort);
      resolve();
    };
    signal.addEventListener("abort", onAbort);
    t = setTimeout(() => {
      signal.removeEventListener("abort", onAbort);
      resolve();
    }, ms);
  });
}
