"use client";

import useSWR from 'swr';
import { useCallback } from 'react';
import { apiGet, apiPost } from '@/lib/api';

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
  positions?: Record<string, any>[];
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
  /** Current open positions array. */
  positions: Record<string, any>[];
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
  const positions = status?.positions ?? [];
  const capital = status?.capital ?? null;
  const balance = status?.balance ?? null;

  // ── Control helpers ───────────────────────────────────────────────────────

  const postControl = useCallback(
    async (action: 'start' | 'pause' | 'resume' | 'stop') => {
      await apiPost(`${baseUrl}/${action}`);
      // Immediately re-fetch status to reflect the new state.
      await mutate();
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
