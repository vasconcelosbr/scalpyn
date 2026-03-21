"use client";

import useSWR from 'swr';
import { useMemo } from 'react';
import { apiGet } from '@/lib/api';

export type TradingProfile = 'spot' | 'futures';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface Position {
  id?: string;
  symbol?: string;
  side?: 'long' | 'short' | string;
  entry_price?: number;
  current_price?: number;
  mark_price?: number;
  size?: number;
  notional?: number;
  unrealised_pnl?: number;
  unrealised_pnl_pct?: number;
  liquidation_price?: number;
  leverage?: number;
  profile?: TradingProfile;
  is_underwater?: boolean;
  [key: string]: any;
}

export interface CapitalSummary {
  totalCapital: number;
  spotCapital: number;
  futuresCapital: number;
  freeCapital: number;
}

export interface UsePositionsReturn {
  /** Positions belonging to the spot engine. */
  spotPositions: Position[];
  /** Positions belonging to the futures engine. */
  futuresPositions: Position[];
  /** Merged array of all positions across both profiles. */
  allPositions: Position[];
  /** Number of positions where unrealised PnL < 0. */
  underwaterCount: number;
  /** Sum of negative unrealised PnL across underwater positions. */
  underwaterValue: number;
  /**
   * Futures position closest to liquidation.
   * "Closest" = smallest absolute distance between current/mark price and liquidation price.
   */
  nearestLiquidation: Position | null;
  /** Capital summary aggregated from both engines. */
  summary: CapitalSummary;
  /** True while either fetch is in flight. */
  isLoading: boolean;
  /** Re-fetch both status endpoints. */
  mutate: () => void;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function toPositionArray(raw: any, profile: TradingProfile): Position[] {
  const list: any[] = Array.isArray(raw?.positions) ? raw.positions : [];
  return list.map((p) => ({ ...p, profile }));
}

function extractCapital(raw: any): { total: number; free: number } {
  const capital = raw?.capital ?? raw?.balance ?? {};
  return {
    total: capital?.total ?? capital?.equity ?? 0,
    free:  capital?.free  ?? capital?.available ?? 0,
  };
}

function liquidationDistance(pos: Position): number {
  const price = pos.mark_price ?? pos.current_price ?? 0;
  const liq   = pos.liquidation_price;
  if (!liq || !price) return Infinity;
  return Math.abs(price - liq);
}

// ---------------------------------------------------------------------------
// Internal single-profile fetcher
// ---------------------------------------------------------------------------

function useProfileStatus(profile: TradingProfile | null) {
  const endpoint = profile ? `/api/${profile}-engine/status` : null;

  const { data, isLoading, mutate } = useSWR(
    endpoint,
    endpoint ? () => apiGet(endpoint) : null,
    {
      refreshInterval: 10_000,
      revalidateOnFocus: true,
      dedupingInterval: 4_000,
    }
  );

  return { data: data ?? null, isLoading, mutate };
}

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

/**
 * Aggregates open positions from one or both trading engines.
 *
 * @param profile - 'spot' | 'futures' to fetch a single profile,
 *                  or undefined to fetch both and merge.
 */
export function usePositions(profile?: TradingProfile): UsePositionsReturn {
  const fetchSpot    = profile === undefined || profile === 'spot';
  const fetchFutures = profile === undefined || profile === 'futures';

  const spot    = useProfileStatus(fetchSpot    ? 'spot'    : null);
  const futures = useProfileStatus(fetchFutures ? 'futures' : null);

  // ── Positions ─────────────────────────────────────────────────────────────
  const spotPositions: Position[]    = useMemo(
    () => (fetchSpot    ? toPositionArray(spot.data,    'spot')    : []),
    [spot.data,    fetchSpot]
  );

  const futuresPositions: Position[] = useMemo(
    () => (fetchFutures ? toPositionArray(futures.data, 'futures') : []),
    [futures.data, fetchFutures]
  );

  const allPositions: Position[] = useMemo(
    () => [...spotPositions, ...futuresPositions],
    [spotPositions, futuresPositions]
  );

  // ── Underwater metrics ────────────────────────────────────────────────────
  const { underwaterCount, underwaterValue } = useMemo(() => {
    const underwater = allPositions.filter(
      (p) => (p.unrealised_pnl ?? 0) < 0 || p.is_underwater === true
    );
    return {
      underwaterCount: underwater.length,
      underwaterValue: underwater.reduce((sum, p) => sum + (p.unrealised_pnl ?? 0), 0),
    };
  }, [allPositions]);

  // ── Nearest liquidation (futures only) ───────────────────────────────────
  const nearestLiquidation: Position | null = useMemo(() => {
    if (futuresPositions.length === 0) return null;
    const withLiq = futuresPositions.filter((p) => p.liquidation_price != null);
    if (withLiq.length === 0) return null;
    return withLiq.reduce<Position>((closest, pos) =>
      liquidationDistance(pos) < liquidationDistance(closest) ? pos : closest
    , withLiq[0]);
  }, [futuresPositions]);

  // ── Capital summary ───────────────────────────────────────────────────────
  const summary: CapitalSummary = useMemo(() => {
    const s = extractCapital(spot.data);
    const f = extractCapital(futures.data);
    return {
      totalCapital:   s.total + f.total,
      spotCapital:    s.total,
      futuresCapital: f.total,
      freeCapital:    s.free  + f.free,
    };
  }, [spot.data, futures.data]);

  // ── Loading ───────────────────────────────────────────────────────────────
  const isLoading = spot.isLoading || futures.isLoading;

  // ── Mutate both ───────────────────────────────────────────────────────────
  const mutate = () => {
    spot.mutate();
    futures.mutate();
  };

  // ── Return ────────────────────────────────────────────────────────────────
  return {
    spotPositions,
    futuresPositions,
    allPositions,
    underwaterCount,
    underwaterValue,
    nearestLiquidation,
    summary,
    isLoading,
    mutate,
  };
}
