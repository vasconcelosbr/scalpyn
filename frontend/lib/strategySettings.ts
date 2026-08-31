import { apiGet, apiPost, apiPut } from "@/lib/api";

export type JsonValue = string | number | boolean | null | JsonObject | JsonValue[];
export interface JsonObject { [key: string]: JsonValue }

export interface StrategyDefinition {
  id: string;
  name: string;
  enabled: boolean;
  params: Record<string, number>;
}

export interface StrategySettingsBundle extends JsonObject {
  schema: string;
  schema_version: number;
  exported_at: string;
  source_hash: string;
  strategy: JsonObject & { strategies: StrategyDefinition[] };
  spot_engine: JsonObject;
  ml_shadow: JsonObject;
}

export interface StrategySettingsResponse {
  config: StrategySettingsBundle;
  catalog: JsonObject;
  persisted: Record<string, boolean>;
}

export interface StrategySettingsDiff {
  path: string;
  before: JsonValue | undefined;
  after: JsonValue | undefined;
}

export interface StrategySettingsValidation {
  valid: boolean;
  source_hash: string;
  config: StrategySettingsBundle;
  diff: StrategySettingsDiff[];
  catalog: JsonObject;
}

export const EDITABLE_ROOTS = ["strategy", "spot_engine", "ml_shadow"] as const;

export const ENUM_OPTIONS: Record<string, string[]> = {
  "spot_engine.scanner.universe_source": ["dynamic", "watchlist", "custom"],
  "spot_engine.scanner.l3_profile_consolidation_rule_version": ["single_profile_per_symbol_v1"],
  "spot_engine.scanner.l3_block_and_skipped_policy": ["legacy", "not_satisfied"],
  "spot_engine.scanner.l3_missing_indicator_policy": ["warn", "disable_rule"],
  "spot_engine.scanner.l3_global_block_range_compiler.policy_version": ["l3_global_block_range_compiler_v1"],
  "spot_engine.buying.order_type": ["market", "limit"],
  "spot_engine.shadow.trailing_contract_version": ["shadow_hwm_trailing_v1"],
  "ml_shadow.shadow_barrier_mode": ["FIXED", "ATR_DYNAMIC"],
  "ml_shadow.shadow_atr_timeframe": ["1m", "5m", "15m", "1h"],
  "ml_shadow.ml_active_barrier_contract_version": ["shadow_fixed_v1", "shadow_atr_dynamic_v2", "shadow_atr_dynamic_v3"],
  "ml_shadow.shadow_barrier_geometry_policy": ["LEGACY_INDEPENDENT_CLAMP", "SL_ANCHORED_RATIO", "ATR_CLAMPED_BEFORE_MULTIPLY"],
  "ml_shadow.shadow_canonical_barrier_policy_version": ["shadow_closed_ohlcv_first_touch_v1"],
};

export function parseStrategySettingsJson(text: string): JsonObject {
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch (error) {
    throw new Error(`JSON inválido: ${error instanceof Error ? error.message : String(error)}`);
  }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error("O JSON deve começar com um objeto.");
  }
  return parsed as JsonObject;
}

export function editablePayload(bundle: StrategySettingsBundle): JsonObject {
  return {
    strategy: structuredClone(bundle.strategy),
    spot_engine: structuredClone(bundle.spot_engine),
    ml_shadow: structuredClone(bundle.ml_shadow),
  };
}

export function updateAtPath<T extends JsonObject>(source: T, path: string[], value: JsonValue): T {
  const clone = structuredClone(source);
  let cursor: JsonObject = clone;
  path.forEach((part, index) => {
    if (index === path.length - 1) {
      cursor[part] = value;
    } else {
      cursor = cursor[part] as JsonObject;
    }
  });
  return clone;
}

export function collectEditableLeafPaths(value: JsonValue, prefix = ""): string[] {
  if (Array.isArray(value)) {
    return value.flatMap((item, index) => collectEditableLeafPaths(item, `${prefix}[${index}]`));
  }
  if (value && typeof value === "object") {
    return Object.entries(value).flatMap(([key, child]) =>
      collectEditableLeafPaths(child, prefix ? `${prefix}.${key}` : key),
    );
  }
  return [prefix];
}

export function normaliseBarrierContract(payload: JsonObject): JsonObject {
  const mode = ((payload.ml_shadow as JsonObject)?.shadow_barrier_mode ?? "ATR_DYNAMIC") as string;
  return updateAtPath(
    payload,
    ["ml_shadow", "ml_active_barrier_contract_version"],
    mode === "ATR_DYNAMIC" ? "shadow_atr_dynamic_v2" : "shadow_fixed_v1",
  );
}

export async function loadStrategySettings(): Promise<StrategySettingsResponse> {
  return apiGet<StrategySettingsResponse>("/strategy-settings/config");
}

export async function validateStrategySettings(
  payload: JsonObject,
  sourceHash?: string,
): Promise<StrategySettingsValidation> {
  return apiPost<StrategySettingsValidation>("/strategy-settings/import/validate", {
    payload,
    source_hash: sourceHash,
  });
}

export async function saveStrategySettings(
  payload: JsonObject,
  sourceHash: string,
  source: "FORM" | "JSON_IMPORT",
) {
  return apiPut<StrategySettingsResponse & { status: string }>("/strategy-settings/config", {
    payload,
    source_hash: sourceHash,
    source,
    change_description: source === "FORM"
      ? "Updated via complete Strategies settings form"
      : "Updated via Strategies JSON import",
  });
}

export async function downloadSavedStrategySettings(): Promise<void> {
  const token = typeof window === "undefined" ? null : localStorage.getItem("token");
  const response = await fetch("/api/strategy-settings/export", {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!response.ok) throw new Error(`Falha ao exportar: HTTP ${response.status}`);
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = "scalpyn-strategy-settings.json";
  link.click();
  URL.revokeObjectURL(url);
}
