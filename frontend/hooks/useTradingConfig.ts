"use client";

import useSWR from 'swr';
import { useCallback, useEffect, useState } from 'react';
import { apiGet, apiPut } from '@/lib/api';

export type TradingProfile = 'spot' | 'futures';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Deep-clone a plain object via JSON round-trip. */
function deepClone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value));
}

/** Structural equality via JSON serialisation. */
function isEqual(a: unknown, b: unknown): boolean {
  return JSON.stringify(a) === JSON.stringify(b);
}

/**
 * Set a nested property in an object using a dot-notation path.
 * Returns a new object (immutable update).
 *
 * Example: setByPath({ buying: { score_threshold: 70 } }, "buying.score_threshold", 80)
 */
function setByPath(obj: Record<string, any>, path: string, value: any): Record<string, any> {
  const keys = path.split('.');
  const next = deepClone(obj);
  let cursor: any = next;

  for (let i = 0; i < keys.length - 1; i++) {
    const key = keys[i];
    if (cursor[key] === null || typeof cursor[key] !== 'object') {
      cursor[key] = {};
    }
    cursor = cursor[key];
  }

  cursor[keys[keys.length - 1]] = value;
  return next;
}

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface UseTradingConfigReturn {
  /** Current locally-edited config (may differ from saved server config). */
  config: Record<string, any>;
  /** Factory defaults fetched from the /default endpoint. */
  defaultConfig: Record<string, any> | null;
  /** Deep-update a field using dot-notation path, e.g. "buying.score_threshold". */
  updateConfig: (path: string, value: any) => void;
  /** Discard local edits and restore the last server-fetched config. */
  resetConfig: () => void;
  /** Persist the current local config to the server via PUT. */
  saveConfig: () => Promise<void>;
  /** True while the initial SWR fetch is in progress. */
  isLoading: boolean;
  /** True while a PUT save request is in flight. */
  isSaving: boolean;
  /** True if the local config differs from the server config. */
  isDirty: boolean;
  /** Any fetch/save error. */
  error: Error | null;
}

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

/**
 * Fetches and manages the trading engine config for the given profile.
 *
 * - profile: 'spot' | 'futures'
 * - Server config:  GET  /config/{profile}_engine
 * - Default config: GET  /api/{profile}-engine/config/default
 * - Save config:    PUT  /config/{profile}_engine
 */
export function useTradingConfig(profile: TradingProfile): UseTradingConfigReturn {
  const configEndpoint = `/config/${profile}_engine`;
  const defaultEndpoint = `/api/${profile}-engine/config/default`;

  // ── Server config via SWR ─────────────────────────────────────────────────
  const {
    data: serverData,
    error: fetchError,
    isLoading,
    mutate,
  } = useSWR<Record<string, any>>(
    configEndpoint,
    () => apiGet(configEndpoint).then((res) => res?.data ?? res),
    {
      revalidateOnFocus: false,
      dedupingInterval: 10_000,
    }
  );

  // ── Default config via SWR ────────────────────────────────────────────────
  const { data: defaultData } = useSWR<Record<string, any>>(
    defaultEndpoint,
    () => apiGet(defaultEndpoint).then((res) => res?.data ?? res),
    {
      revalidateOnFocus: false,
      dedupingInterval: 60_000,
    }
  );

  // ── Local edited state ────────────────────────────────────────────────────
  const [localConfig, setLocalConfig] = useState<Record<string, any>>({});
  const [isSaving, setIsSaving] = useState(false);
  const [saveError, setSaveError] = useState<Error | null>(null);

  // Seed / sync local state whenever fresh server data arrives.
  useEffect(() => {
    if (serverData && Object.keys(serverData).length > 0) {
      setLocalConfig((prev) => {
        // Only overwrite if local state is empty (first load) or matches server
        // (no pending edits). This prevents wiping unsaved changes on revalidation.
        if (Object.keys(prev).length === 0 || isEqual(prev, serverData)) {
          return deepClone(serverData);
        }
        return prev;
      });
    }
  }, [serverData]);

  // ── Derived ──────────────────────────────────────────────────────────────
  const isDirty =
    serverData != null &&
    Object.keys(localConfig).length > 0 &&
    !isEqual(localConfig, serverData);

  // ── Actions ───────────────────────────────────────────────────────────────

  const updateConfig = useCallback((path: string, value: any) => {
    setLocalConfig((prev) => setByPath(prev, path, value));
  }, []);

  const resetConfig = useCallback(() => {
    if (serverData) {
      setLocalConfig(deepClone(serverData));
    }
  }, [serverData]);

  const saveConfig = useCallback(async () => {
    if (!localConfig || Object.keys(localConfig).length === 0) return;
    setIsSaving(true);
    setSaveError(null);
    try {
      await apiPut(configEndpoint, localConfig);
      // Update SWR cache so isDirty immediately becomes false.
      await mutate(deepClone(localConfig), { revalidate: false });
    } catch (err) {
      const error = err instanceof Error ? err : new Error(String(err));
      setSaveError(error);
      throw error;
    } finally {
      setIsSaving(false);
    }
  }, [configEndpoint, localConfig, mutate]);

  // ── Return ────────────────────────────────────────────────────────────────
  return {
    config: localConfig,
    defaultConfig: defaultData ?? null,
    updateConfig,
    resetConfig,
    saveConfig,
    isLoading,
    isSaving,
    isDirty,
    error: saveError ?? (fetchError instanceof Error ? fetchError : fetchError ? new Error(String(fetchError)) : null),
  };
}
