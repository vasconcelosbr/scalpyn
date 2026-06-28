"use client";

import useSWR from "swr";
import { apiGet } from "@/lib/api";

/**
 * Shape returned by ``GET /api/live/balance``.
 *
 * Numeric fields are pre-rounded server-side to 8 decimals; we keep
 * them as ``number`` here and let the formatter decide presentation.
 */
export interface LiveBalance {
  available_usdt: number;
  in_positions: number;
  total: number;
  updated_at: string;
  /** ``"exchange"`` | ``"exchange_error"`` | ``"no_connection"`` */
  source: string;
  /** Human-readable adapter error when ``source === "exchange_error"``. */
  error: string | null;
}

export interface UseLiveBalanceReturn {
  data: LiveBalance | null;
  isLoading: boolean;
  error: Error | null;
  mutate: () => void;
}

/**
 * Polling wrapper around ``/api/live/balance`` (10 s interval per spec).
 *
 * Uses SWR's built-in ``refreshInterval`` so a single in-flight request
 * is shared across components mounting the hook simultaneously
 * (``BalanceMetrics`` is the only consumer today, but the dedup is
 * still worth having).
 */
export function useLiveBalance(): UseLiveBalanceReturn {
  const { data, isLoading, error, mutate } = useSWR<LiveBalance>(
    "/api/live/balance",
    (url: string) => apiGet<LiveBalance>(url),
    {
      refreshInterval: 10_000,
      revalidateOnFocus: false, // saldo refetched a cada 10s; burst em foco desperdiça conexão
      dedupingInterval: 8_000,
      keepPreviousData: true,
    }
  );

  return {
    data: data ?? null,
    isLoading,
    error: (error as Error) ?? null,
    mutate: () => mutate(),
  };
}
