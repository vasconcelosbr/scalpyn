"use client";

import useSWR from 'swr';
import { useCallback } from 'react';
import { apiGet, apiPost } from '@/lib/api';
import { extractPositions, extractPositionsSummary } from '@/lib/engineStatus';

export type TradingProfile = 'spot' | 'futures';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface EngineInfo {
  running: boolean;
  paused: boolean;
  state?: string;
  uptime_seconds?: number;
  [key: string]: any;
}

export interface CapitalInfo {
  total: number;
  used: number;
  free: number;
  [key: string]: any;
}

export interface EngineStatusResponse {
  engine?: EngineInfo;
  /** Raw payload — read via `extractPositions` / `extractPositionsSummary`. */
  positions?: unknown;
  capital?: CapitalInfo;
  balance?: Record<string, any>;
  [key: string]: any;
}

export interface UseEngineStatusReturn {
  /** Full raw API response from the status endpoint. */
  status: EngineStatusResponse | null;
  /** True if the engine is currently running (not stopped). */
  isRunning: boolean;
  /** True if the engine is paused. */
  isPaused: boolean;
  /** Always an array — normalised from the raw payload. */
  positions: Record<string, any>[];
  /** Original dict payload (spot/futures summary) or null when it was an array/missing. */
  positionsSummary: Record<string, any> | null;
  /** Capital summary from the status response. */
  capital: CapitalInfo | null;
  /** Balance information from the status response. */
  balance: Record<string, any> | null;
  /** True while the initial SWR fetch is in progress. */
  isLoading: boolean;
  /** Any error from the status fetch. */
  error: Error | null;
  /** Start the engine. */
  startEngine: () => Promise<void>;
  /** Pause a running engine. */
  pauseEngine: () => Promise<void>;
  /** Resume a paused engine. */
  resumeEngine: () => Promise<void>;
  /** Gracefully stop the engine. */
  stopEngine: () => Promise<void>;
  /** Manually re-fetch the status. */
  mutate: () => void;
}

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

/**
 * Polls the engine status every 5 seconds and exposes lifecycle control functions.
 *
 * - profile: 'spot' | 'futures'
 * - Status endpoint: GET /api/{profile}-engine/status
 * - Control endpoints:
 *     POST /api/{profile}-engine/start
 *     POST /api/{profile}-engine/pause
 *     POST /api/{profile}-engine/resume
 *     POST /api/{profile}-engine/stop
 */
export function useEngineStatus(profile: TradingProfile): UseEngineStatusReturn {
  const baseUrl = `/api/${profile}-engine`;
  const statusEndpoint = `${baseUrl}/status`;

  const {
    data,
    error: swrError,
    isLoading,
    mutate,
  } = useSWR<EngineStatusResponse>(
    statusEndpoint,
    () => apiGet<EngineStatusResponse>(statusEndpoint),
    {
      refreshInterval: 5_000,
      revalidateOnFocus: true,
      dedupingInterval: 2_000,
    }
  );

  // ── Derived state ─────────────────────────────────────────────────────────
  const status = data ?? null;
  const isRunning = status?.engine?.running ?? false;
  const isPaused = status?.engine?.paused ?? false;
  const rawPositions = status?.positions;
  const positions = extractPositions(rawPositions);
  const positionsSummary = extractPositionsSummary(rawPositions);
  const capital = status?.capital ?? null;
  const balance = status?.balance ?? null;

  // ── Control helpers ───────────────────────────────────────────────────────

  const postControl = useCallback(
    async (action: 'start' | 'pause' | 'resume' | 'stop') => {
      await apiPost(`${baseUrl}/${action}`);
      // Action succeeded — refresh status best-effort; SWR will reconcile
      // on the next poll if this throws.
      try {
        await mutate();
      } catch (refreshErr) {
        // eslint-disable-next-line no-console
        console.warn(`[engine] post-${action} status refresh failed:`, refreshErr);
      }
    },
    [baseUrl, mutate]
  );

  const startEngine  = useCallback(() => postControl('start'),  [postControl]);
  const pauseEngine  = useCallback(() => postControl('pause'),  [postControl]);
  const resumeEngine = useCallback(() => postControl('resume'), [postControl]);
  const stopEngine   = useCallback(() => postControl('stop'),   [postControl]);

  // ── Normalise error ───────────────────────────────────────────────────────
  const error: Error | null =
    swrError instanceof Error
      ? swrError
      : swrError
      ? new Error(String(swrError))
      : null;

  // ── Return ────────────────────────────────────────────────────────────────
  return {
    status,
    isRunning,
    isPaused,
    positions,
    positionsSummary,
    capital,
    balance,
    isLoading,
    error,
    startEngine,
    pauseEngine,
    resumeEngine,
    stopEngine,
    mutate: () => { mutate(); },
  };
}
