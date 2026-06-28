"use client";

import useSWR from "swr";
import { apiGet } from "@/lib/api";

/**
 * One row of ``GET /api/live/positions`` — the open-trade view fed by
 * ``trades`` LEFT JOIN ``market_metadata`` on the backend.
 *
 * Nullable columns reflect real states: ``current_price`` is ``null``
 * when ``market_metadata`` hasn't been populated yet (collector cold
 * start), in which case ``pnl_*`` and ``margin_to_target_pct`` are
 * also ``null`` and ``status_label`` flips to ``"aguardando"``.
 */
export interface LivePosition {
  trade_id: string;
  symbol: string;
  entry_price: number;
  current_price: number | null;
  quantity: number;
  invested_value: number | null;
  pnl_usdt: number | null;
  pnl_pct: number | null;
  tp_price: number | null;
  margin_to_target_pct: number | null;
  status: string;
  /** ``"holding"`` | ``"underwater"`` | ``"aguardando"`` */
  status_label: "holding" | "underwater" | "aguardando" | string;
}

export interface LivePositionsResponse {
  items: LivePosition[];
  count: number;
  updated_at: string;
}

export interface UseLivePositionsReturn {
  positions: LivePosition[];
  count: number;
  updatedAt: string | null;
  isLoading: boolean;
  error: Error | null;
  mutate: () => void;
}

/**
 * Polling wrapper around ``/api/live/positions`` (5 s per spec).
 *
 * NOTE: this is intentionally a NEW hook and does not replace the
 * existing ``usePositions.ts``. The legacy hook fans out across
 * ``/api/spot-engine/status`` + ``/api/futures-engine/status`` and
 * exposes engine-specific shape (liquidation_price, leverage, etc).
 * The diagnostics page needs a unified ``trades`` row joined with
 * the latest market price + TP progress, which is what the
 * ``/api/live/positions`` endpoint computes.
 */
export function useLivePositions(): UseLivePositionsReturn {
  const { data, isLoading, error, mutate } = useSWR<LivePositionsResponse>(
    "/api/live/positions",
    (url: string) => apiGet<LivePositionsResponse>(url),
    {
      refreshInterval: 5_000,
      revalidateOnFocus: true,
      dedupingInterval: 2_000,
      keepPreviousData: true,
    }
  );

  return {
    positions: data?.items ?? [],
    count: data?.count ?? 0,
    updatedAt: data?.updated_at ?? null,
    isLoading,
    error: (error as Error) ?? null,
    mutate: () => mutate(),
  };
}
