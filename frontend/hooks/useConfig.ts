"use client";

import useSWR from "swr";
import { apiGet, apiPut } from "@/lib/api";
import { useCallback } from "react";

/**
 * Hook for fetching and updating a config_type from the Scalpyn ConfigService.
 *
 * Usage:
 *   const { config, updateConfig, isLoading, error } = useConfig("risk");
 */
export function useConfig(configType: string, poolId?: string) {
  const endpoint = poolId
    ? `/config/${configType}?pool_id=${poolId}`
    : `/config/${configType}`;

  const { data, error, isLoading, mutate } = useSWR(
    endpoint,
    () => apiGet(endpoint).then((res) => res.data),
    { revalidateOnFocus: false, dedupingInterval: 5000 }
  );

  const updateConfig = useCallback(
    async (newConfig: Record<string, any>) => {
      const updateEndpoint = poolId
        ? `/config/${configType}?pool_id=${poolId}`
        : `/config/${configType}`;
      await apiPut(updateEndpoint, newConfig);
      await mutate();
    },
    [configType, poolId, mutate]
  );

  const resetConfig = useCallback(async () => {
    // POST reset endpoint would reload defaults
    await mutate();
  }, [mutate]);

  return {
    config: data ?? {},
    updateConfig,
    resetConfig,
    isLoading,
    error,
    mutate,
  };
}
