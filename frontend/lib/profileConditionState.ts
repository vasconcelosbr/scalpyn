import { STRATEGY_PROFILE_INDICATOR_MAP } from "./indicatorCatalog";

export type ProfileRuleConditionType = "threshold" | "boolean" | "comparison";
export type ProfileFeatureSource =
  | "ohlcv"
  | "live_trade_flow"
  | "live_order_book"
  | "decision_context";

export type ProfileSourcePolicy = Record<string, unknown> & {
  allowed_source_providers?: unknown[];
  provider_policy_id?: unknown;
  max_age_seconds?: unknown;
  timeframe?: unknown;
  window_seconds?: unknown;
  snapshot?: unknown;
  candle_policy?: unknown;
};

export type ProfileSourcePolicies = Partial<Record<ProfileFeatureSource, ProfileSourcePolicy>>;

export interface PreparedProfileEditorConfig<T> {
  config: T;
  issues: string[];
}
export type ProfileRuleConditionState = Record<string, unknown> & {
  id: string;
  type: ProfileRuleConditionType;
  operator: string;
  indicator?: string;
  left?: string;
  right?: string;
};

export function isProfileComparisonCondition(
  condition: {
    type?: ProfileRuleConditionType;
    left?: string;
    right?: string;
  },
): boolean {
  return condition.type === "comparison" || Boolean(condition.left && condition.right);
}

export function profileConditionPrimaryIndicator(
  condition: {
    type?: ProfileRuleConditionType;
    left?: string;
    right?: string;
    indicator?: string;
    field?: string;
  },
): string {
  return isProfileComparisonCondition(condition)
    ? String(condition.left || condition.field || "price")
    : String(condition.indicator || condition.field || "rsi");
}

/** Normalize editor state while retaining every source field verbatim. */
export function normalizeProfileRuleCondition(
  rawValue: unknown,
  fallbackId = `cond_${Date.now()}`,
): ProfileRuleConditionState {
  const raw = rawValue && typeof rawValue === "object" && !Array.isArray(rawValue)
    ? rawValue as Record<string, unknown>
    : {};

  if (raw.type === "comparison" || (raw.left && raw.right)) {
    return {
      ...raw,
      id: String(raw.id || fallbackId),
      type: "comparison",
      left: String(raw.left || "price"),
      operator: String(raw.operator || ">"),
      right: String(raw.right || "ema9"),
    };
  }

  const indicator = String(raw.indicator || raw.field || "rsi");
  const catalogKind = STRATEGY_PROFILE_INDICATOR_MAP.get(indicator)?.kind;
  const inferredType: ProfileRuleConditionType =
    raw.type === "boolean" || catalogKind === "boolean" ||
    raw.operator === "is_true" || raw.operator === "is_false" || typeof raw.value === "boolean"
      ? "boolean"
      : "threshold";

  const normalized: ProfileRuleConditionState = {
    ...raw,
    id: String(raw.id || fallbackId),
    type: inferredType,
    indicator,
    operator: String(raw.operator || (inferredType === "boolean" ? "is_true" : "<")),
  };
  if (Object.prototype.hasOwnProperty.call(raw, "value")) {
    normalized.value = raw.value;
  }
  return normalized;
}

export function updateProfileRuleCondition<T extends Record<string, unknown>>(
  condition: T,
  updates: Partial<T>,
): T {
  return { ...condition, ...updates };
}

/** Remove identity that belongs to a feature before changing its indicator. */
export function withoutProfileFeatureIdentity<T extends Record<string, unknown>>(
  condition: T,
): T {
  const next = { ...condition };
  for (const key of [
    "source", "source_provider", "provider_policy_id", "max_age_seconds",
    "window_seconds", "snapshot", "candle_policy", "resolved_operands", "period", "parameters",
  ]) {
    delete next[key];
  }
  return next;
}

const LIVE_TRADE_FLOW_INDICATORS = new Set([
  "taker_ratio", "volume_delta", "buy_pressure",
  "taker_buy_volume", "taker_sell_volume",
]);
const LIVE_ORDER_BOOK_INDICATORS = new Set([
  "orderbook_pressure", "bid_ask_imbalance", "orderbook_depth_usdt",
  "spread_pct", "spread",
]);
const DECISION_CONTEXT_INDICATORS = new Set([
  "price", "change_24h", "market_cap", "volume_24h",
  "alpha_score", "score", "liquidity_score", "momentum_score",
]);
const SCORE_INDICATORS = new Set([
  "alpha_score", "score", "liquidity_score", "momentum_score",
]);

function inferredFeatureSource(
  indicator: string,
  comparisonOperand = false,
): ProfileFeatureSource {
  if (LIVE_TRADE_FLOW_INDICATORS.has(indicator)) return "live_trade_flow";
  if (LIVE_ORDER_BOOK_INDICATORS.has(indicator)) return "live_order_book";
  if (DECISION_CONTEXT_INDICATORS.has(indicator) && !(comparisonOperand && indicator === "price")) {
    return "decision_context";
  }
  return "ohlcv";
}

/** A different indicator must not inherit the previous producer or window. */
export function updateProfileConditionIndicator<T extends Record<string, any>>(condition: T, updates: Partial<T>): T {
  const next: Record<string, any> = { ...condition, ...updates };
  if (updates.field !== undefined && condition.indicator !== undefined) next.indicator = updates.field;
  const indicator = profileConditionPrimaryIndicator(next);
  const changed = indicator !== profileConditionPrimaryIndicator(condition);
  if (!changed) return next as T;
  const cleaned = withoutProfileFeatureIdentity(next);
  if (inferredFeatureSource(indicator, isProfileComparisonCondition(next)) !== "ohlcv") delete cleaned.timeframe;
  return cleaned as T;
}

/** Presentation of configured identity, never a claim of live data availability. */
export function profileTemporalIdentity(
  condition: Record<string, any>,
  policies: ProfileSourcePolicies = {},
  defaultTimeframe = "",
) {
  const indicator = profileConditionPrimaryIndicator(condition);
  const reference = isProfileComparisonCondition(condition)
    ? (condition.resolved_operands?.left || {}) : condition;
  const source = reference.source || inferredFeatureSource(indicator, isProfileComparisonCondition(condition));
  const policy = policies[source as ProfileFeatureSource] || {};
  const configured = Boolean(reference.source);
  const provenance = configured ? "Identidade configurada" : "Política de preenchimento; identidade ainda não validada";
  const base = { source, windowSeconds: undefined as number | undefined, showTimeframe: false, showPeriod: false,
    label: "Contexto da decisão", detail: provenance, needsValidation: !configured };
  if (source === "live_trade_flow") {
    const window = reference.window_seconds ?? policy.window_seconds;
    const validWindow = typeof window === "number" && Number.isFinite(window) && window > 0;
    const differs = validWindow && policy.window_seconds != null && window !== policy.window_seconds;
    return { ...base, windowSeconds: validWindow ? window : undefined,
      label: validWindow ? `Janela de fluxo: ${window} s` : "Janela de fluxo não definida",
      needsValidation: !configured || !validWindow || differs,
      detail: differs ? `${provenance}. Difere da política (${policy.window_seconds} s); verificar disponibilidade no contrato.` : provenance };
  }
  if (source === "live_order_book") {
    const snapshot = reference.snapshot ?? policy.snapshot;
    const window = reference.window_seconds ?? policy.window_seconds;
    return { ...base,
      label: snapshot === true ? "Snapshot do livro" : "Identidade do livro não confirmada",
      needsValidation: !configured || snapshot !== true || window != null,
      detail: window != null ? `${provenance}. Janela configurada: ${window} s; suporte do produtor não confirmado.` : provenance };
  }
  if (source === "ohlcv") {
    const timeframe = reference.timeframe || condition.timeframe || policy.timeframe || defaultTimeframe;
    const candlePolicy = reference.candle_policy || policy.candle_policy;
    const candleLabel = candlePolicy === "CLOSED_ONLY" ? "Candles fechados" : candlePolicy === "CURRENT_ALLOWED" ? "Candle atual permitido" : "Política de candle não definida";
    return { ...base, showTimeframe: !STRATEGY_PROFILE_INDICATOR_MAP.get(indicator)?.noTimeframe,
      showPeriod: true, label: `${candleLabel}${timeframe ? ` · ${timeframe}` : ""}` };
  }
  return base;
}

function configuredProvider(
  indicator: string,
  existing: Record<string, unknown>,
  policy: ProfileSourcePolicy,
): string | undefined {
  const allowed = (policy.allowed_source_providers || [])
    .map((value) => String(value || "").trim())
    .filter(Boolean);
  const current = String(existing.source_provider || "").trim();
  if (current && (allowed.length === 0 || allowed.includes(current))) return current;
  if (SCORE_INDICATORS.has(indicator) && allowed.includes("robust_score")) return "robust_score";
  if (allowed.includes("market_metadata")) return "market_metadata";
  return allowed[0];
}

function conditionIdentity(
  indicator: string,
  condition: Record<string, any>,
  existing: Record<string, any>,
  policies: ProfileSourcePolicies,
  defaultTimeframe: string,
  path: string,
  issues: string[],
  comparisonOperand: "left" | "right" | false = false,
): Record<string, any> {
  const existingMatches = !existing.indicator || existing.indicator === indicator;
  const source = (
    existingMatches && typeof existing.source === "string" && existing.source.trim()
      ? existing.source.trim()
      : inferredFeatureSource(indicator, Boolean(comparisonOperand))
  ) as ProfileFeatureSource;
  const policy = policies[source] || {};
  const reference: Record<string, any> = {
    ...(existingMatches ? existing : {}),
    indicator,
    source,
  };
  reference.source_provider = configuredProvider(indicator, reference, policy);
  reference.provider_policy_id = reference.provider_policy_id || policy.provider_policy_id;
  reference.max_age_seconds = reference.max_age_seconds ?? policy.max_age_seconds;

  if (source === "ohlcv") {
    const catalog = STRATEGY_PROFILE_INDICATOR_MAP.get(indicator);
    // Only an explicit producer contract permits removing saved identity.
    // Missing UI defaults do not invalidate MACD/VWAP parameters or slope periods.
    if (catalog?.calculationIdentity === "indicator_name") {
      delete reference.period;
      delete reference.parameters;
    } else {
      // The editor's shared period belongs to the left operand; the right
      // operand must retain its own calculation identity.
      const configuredPeriod = (comparisonOperand === "right" ? undefined : condition.period)
        ?? reference.period ?? catalog?.fixedPeriod ?? catalog?.defaultPeriod;
      if (configuredPeriod !== undefined) reference.period = configuredPeriod;
      const configuredParameters = (comparisonOperand === "right" ? undefined : condition.parameters)
        ?? reference.parameters ?? catalog?.defaultParameters;
      if (configuredParameters !== undefined) reference.parameters = configuredParameters;
    }
    reference.timeframe = condition.timeframe || reference.timeframe || policy.timeframe || defaultTimeframe;
    reference.candle_policy = reference.candle_policy || policy.candle_policy;
    delete reference.window_seconds;
    delete reference.snapshot;
  } else if (source === "live_trade_flow") {
    reference.window_seconds = reference.window_seconds ?? policy.window_seconds;
    delete reference.period;
    delete reference.parameters;
    delete reference.timeframe;
    delete reference.snapshot;
    delete reference.candle_policy;
  } else if (source === "live_order_book") {
    reference.snapshot = reference.snapshot ?? policy.snapshot;
    reference.window_seconds = reference.window_seconds ?? policy.window_seconds;
    delete reference.period;
    delete reference.parameters;
    delete reference.timeframe;
    delete reference.candle_policy;
  } else {
    delete reference.period;
    delete reference.parameters;
    delete reference.timeframe;
    delete reference.window_seconds;
    delete reference.snapshot;
    delete reference.candle_policy;
  }

  if (!reference.source_provider) issues.push(`${path}.source_provider`);
  if (!reference.provider_policy_id) issues.push(`${path}.provider_policy_id`);
  if (condition.required === true && reference.max_age_seconds == null) {
    issues.push(`${path}.max_age_seconds`);
  }
  if (source === "ohlcv") {
    if (!reference.timeframe) issues.push(`${path}.timeframe`);
    if (!reference.candle_policy) issues.push(`${path}.candle_policy`);
  } else if (source === "live_trade_flow" && reference.window_seconds == null) {
    issues.push(`${path}.window_seconds`);
  } else if (
    source === "live_order_book"
    && reference.snapshot !== true
    && reference.window_seconds == null
  ) {
    issues.push(`${path}.snapshot_or_window_seconds`);
  }
  return reference;
}

/** Return the governed source policies applicable to the profile being edited. */
export function profileSourcePoliciesForEditor(
  spotEngineConfig: Record<string, any> | null | undefined,
  profileType: unknown,
  profileRole: unknown,
): ProfileSourcePolicies {
  const scanner = spotEngineConfig?.scanner || {};
  const levelByRole: Record<string, string> = {
    primary_filter: "L1",
    score_engine: "L2",
    acquisition_queue: "L3",
  };
  const level = levelByRole[String(profileRole || "")];
  if (profileType === "MTF_LAYER" && !level) return {};
  const layer = scanner?.multilayer_contract?.layers?.[level || "L3"] || {};
  const layerPolicies = layer?.source_policies || {};
  const validityMargins = layer?.validity_margin_seconds_by_group || {};
  const sourceGroup: Partial<Record<ProfileFeatureSource, string>> = {
    ohlcv: "structural",
    live_trade_flow: "microstructure",
    live_order_book: "microstructure",
  };
  const withLayerValidity = (rawPolicies: Record<string, any>): ProfileSourcePolicies => (
    Object.fromEntries(
      Object.entries(rawPolicies).map(([source, rawPolicy]) => {
        const policy = rawPolicy && typeof rawPolicy === "object"
          ? rawPolicy as ProfileSourcePolicy
          : {};
        const group = sourceGroup[source as ProfileFeatureSource];
        const layerMaxAge = (group && validityMargins[group] != null)
          ? validityMargins[group]
          : layer?.validity_margin_seconds;
        return [source, {
          ...policy,
          ...(policy.max_age_seconds == null && layerMaxAge != null
            ? { max_age_seconds: layerMaxAge }
            : {}),
          ...(source === "ohlcv" && !policy.timeframe && layer?.default_timeframe
            ? { timeframe: layer.default_timeframe }
            : {}),
        }];
      }),
    ) as ProfileSourcePolicies
  );
  const policiesWithLayerValidity = withLayerValidity(layerPolicies);
  if (profileType === "MTF_LAYER") {
    return policiesWithLayerValidity;
  }
  const resolverPolicies = scanner?.l3_v3_provenance_resolver?.source_policies || {};
  const configuredOnly = (rawPolicies: Record<string, any>) => Object.fromEntries(
    Object.entries(rawPolicies).filter(([, policy]: [string, any]) => Boolean(
      policy?.provider_policy_id
      || policy?.source_provider
      || (Array.isArray(policy?.allowed_source_providers)
        && policy.allowed_source_providers.length > 0),
    )),
  );
  const compilerPolicies = scanner?.l3_global_block_range_compiler?.source_policies || {};
  return withLayerValidity({
    ...configuredOnly(compilerPolicies),
    ...layerPolicies,
    ...configuredOnly(resolverPolicies),
  });
}

/** Address legacy debt by feature (indicator/timeframe/period), not array index. */
/**
 * A condition that already went through governed identity resolution once
 * (it carries a `source`) but is still missing a period/parameters its
 * indicator's catalog entry now defaults is incomplete, not merely
 * "unchanged" -- e.g. an older bb_upper_distance_pct saved before
 * defaultPeriod/defaultParameters existed for it. Such a condition must
 * never take the "same feature, keep as-is" fast path below: doing so
 * would silently perpetuate the missing period/parameters forever, since
 * its own (incomplete) key always matches itself.
 *
 * Scoped to conditions that already carry `source`: a fully legacy
 * condition (no source at all, predating governed identity entirely) is
 * intentionally left alone by the fast path below -- see "editing a
 * legacy profile does not require identity for untouched Entry Triggers".
 */
function _needsIdentityBackfill(condition: Record<string, any>): boolean {
  if (isProfileComparisonCondition(condition)) {
    const operands = condition.resolved_operands || {};
    const sides = condition.operator === "between" ? ["left"] : ["left", "right"];
    return sides.some((side) => {
      const operand = operands[side] || {};
      const indicator = String(condition[side] || (side === "left" ? "price" : "ema9"));
      return STRATEGY_PROFILE_INDICATOR_MAP.get(indicator)?.calculationIdentity === "indicator_name"
        && Boolean(operand.source || condition.source)
        && (operand.period != null || operand.parameters != null
          || (side === "left" && (condition.period != null || condition.parameters != null)));
    });
  }
  if (!condition.source) return false;
  const indicator = String(condition.indicator || condition.field || "");
  const catalog = STRATEGY_PROFILE_INDICATOR_MAP.get(indicator);
  if (!catalog) return false;
  if (condition.period == null && (catalog.fixedPeriod !== undefined || catalog.defaultPeriod !== undefined)) {
    return true;
  }
  if (catalog.calculationIdentity === "indicator_name"
    && (condition.period != null || condition.parameters != null)) {
    return true;
  }
  if (condition.parameters == null && catalog.defaultParameters !== undefined) {
    return true;
  }
  return false;
}

function _conditionFeatureKey(condition: Record<string, any>): string {
  return JSON.stringify(
    isProfileComparisonCondition(condition)
      ? {
          type: "comparison",
          left: condition.left || "price",
          right: condition.operator === "between" ? null : (condition.right || "ema9"),
          between: condition.operator === "between",
          timeframe: condition.timeframe || null,
          period: condition.period ?? null,
          reference_window: condition.reference_window ?? null,
        }
      : {
          type: condition.type || "threshold",
          indicator: condition.indicator || condition.field || "rsi",
          timeframe: condition.timeframe || null,
          period: condition.period ?? null,
          reference_window: condition.reference_window ?? null,
        },
  );
}

/**
 * Complete hidden feature identity for one flat condition list from governed
 * DB config. A condition already addressing the same feature (indicator,
 * timeframe, period) as one in `currentFeatureCounts` keeps its existing
 * identity untouched; anything new gets a freshly derived one, consuming one
 * count from the shared pool so debt isn't double-forgiven across sections.
 */
function _materializeConditionIdentities(
  conditions: Record<string, any>[],
  currentFeatureCounts: Map<string, number>,
  policies: ProfileSourcePolicies,
  defaultTimeframe: string,
  pathPrefix: string,
  issues: string[],
): Record<string, any>[] {
  return conditions.map((raw, index) => {
    const condition = { ...raw };
    const key = _conditionFeatureKey(condition);
    const matchingCurrentCount = currentFeatureCounts.get(key) || 0;
    if (matchingCurrentCount > 0 && !_needsIdentityBackfill(condition)) {
      currentFeatureCounts.set(key, matchingCurrentCount - 1);
      return condition;
    }
    const path = `${pathPrefix}[${index}]`;
    if (isProfileComparisonCondition(condition)) {
      const operands = condition.resolved_operands || {};
      const left = String(condition.left || "price");
      const right = String(condition.right || "ema9");
      const resolvedOperands: Record<string, any> = {
        left: conditionIdentity(
          left, condition, operands.left || {}, policies, defaultTimeframe,
          `${path}.resolved_operands.left`, issues, "left",
        ),
      };
      if (condition.operator !== "between") {
        resolvedOperands.right = conditionIdentity(
          right, condition, operands.right || {}, policies, defaultTimeframe,
          `${path}.resolved_operands.right`, issues, "right",
        );
      }
      condition.resolved_operands = resolvedOperands;
      if (STRATEGY_PROFILE_INDICATOR_MAP.get(left)?.calculationIdentity === "indicator_name") {
        delete condition.period;
        delete condition.parameters;
      }
      const leftIdentity = resolvedOperands.left;
      for (const key of [
        "source", "source_provider", "provider_policy_id", "max_age_seconds",
        "timeframe", "window_seconds", "snapshot", "candle_policy",
      ]) {
        if (leftIdentity[key] === undefined) delete condition[key];
        else condition[key] = leftIdentity[key];
      }
      return condition;
    }

    const indicator = String(condition.indicator || condition.field || "rsi");
    return conditionIdentity(
      indicator, condition, condition, policies, defaultTimeframe, path, issues,
    );
  });
}

/**
 * Complete hidden feature identity for Entry Triggers from governed DB config.
 * Existing identity is retained when it still addresses the same indicator.
 */
export function prepareProfileEntryTriggerIdentities<T extends Record<string, any>>(
  config: T,
  policies: ProfileSourcePolicies,
  currentConfig?: Record<string, any> | null,
): PreparedProfileEditorConfig<T> {
  const issues: string[] = [];
  const defaultTimeframe = String(config.default_timeframe || "");
  const currentFeatureCounts = new Map<string, number>();
  for (const condition of currentConfig?.entry_triggers?.conditions || []) {
    const key = _conditionFeatureKey(condition);
    currentFeatureCounts.set(key, (currentFeatureCounts.get(key) || 0) + 1);
  }
  const conditions = _materializeConditionIdentities(
    config.entry_triggers?.conditions || [],
    currentFeatureCounts,
    policies,
    defaultTimeframe,
    "entry_triggers.conditions",
    issues,
  );
  return {
    config: {
      ...config,
      entry_triggers: { ...(config.entry_triggers || {}), conditions },
    },
    issues: [...new Set(issues)],
  } as PreparedProfileEditorConfig<T>;
}

/**
 * Complete hidden feature identity for Filters from governed DB config,
 * mirroring prepareProfileEntryTriggerIdentities.
 */
export function prepareProfileFilterIdentities<T extends Record<string, any>>(
  config: T,
  policies: ProfileSourcePolicies,
  currentConfig?: Record<string, any> | null,
): PreparedProfileEditorConfig<T> {
  const issues: string[] = [];
  const defaultTimeframe = String(config.default_timeframe || "");
  const currentFeatureCounts = new Map<string, number>();
  for (const condition of currentConfig?.filters?.conditions || []) {
    const key = _conditionFeatureKey(condition);
    currentFeatureCounts.set(key, (currentFeatureCounts.get(key) || 0) + 1);
  }
  const conditions = _materializeConditionIdentities(
    config.filters?.conditions || [],
    currentFeatureCounts,
    policies,
    defaultTimeframe,
    "filters.conditions",
    issues,
  );
  return {
    config: {
      ...config,
      filters: { ...(config.filters || {}), conditions },
    },
    issues: [...new Set(issues)],
  } as PreparedProfileEditorConfig<T>;
}

/**
 * Complete hidden feature identity for Signals from governed DB config,
 * mirroring prepareProfileEntryTriggerIdentities.
 */
export function prepareProfileSignalIdentities<T extends Record<string, any>>(
  config: T,
  policies: ProfileSourcePolicies,
  currentConfig?: Record<string, any> | null,
): PreparedProfileEditorConfig<T> {
  const issues: string[] = [];
  const defaultTimeframe = String(config.default_timeframe || "");
  const currentFeatureCounts = new Map<string, number>();
  for (const condition of currentConfig?.signals?.conditions || []) {
    const key = _conditionFeatureKey(condition);
    currentFeatureCounts.set(key, (currentFeatureCounts.get(key) || 0) + 1);
  }
  const conditions = _materializeConditionIdentities(
    config.signals?.conditions || [],
    currentFeatureCounts,
    policies,
    defaultTimeframe,
    "signals.conditions",
    issues,
  );
  return {
    config: {
      ...config,
      signals: { ...(config.signals || {}), conditions },
    },
    issues: [...new Set(issues)],
  } as PreparedProfileEditorConfig<T>;
}

/**
 * Complete hidden feature identity for Block Rules from governed DB config,
 * mirroring prepareProfileEntryTriggerIdentities. Existing debt is matched
 * by feature across the whole block_rules tree (not block position), since
 * blocks can be reordered or renamed without changing what they evaluate.
 */
export function prepareProfileBlockRuleIdentities<T extends Record<string, any>>(
  config: T,
  policies: ProfileSourcePolicies,
  currentConfig?: Record<string, any> | null,
): PreparedProfileEditorConfig<T> {
  const issues: string[] = [];
  const defaultTimeframe = String(config.default_timeframe || "");
  const currentFeatureCounts = new Map<string, number>();
  for (const block of currentConfig?.block_rules?.blocks || []) {
    for (const condition of block?.conditions || []) {
      const key = _conditionFeatureKey(condition);
      currentFeatureCounts.set(key, (currentFeatureCounts.get(key) || 0) + 1);
    }
  }
  const blocks = (config.block_rules?.blocks || []).map(
    (block: Record<string, any>, blockIndex: number) => ({
      ...block,
      conditions: _materializeConditionIdentities(
        block.conditions || [],
        currentFeatureCounts,
        policies,
        defaultTimeframe,
        `block_rules.blocks[${blockIndex}].conditions`,
        issues,
      ),
    }),
  );
  return {
    config: {
      ...config,
      block_rules: { ...(config.block_rules || {}), blocks },
    },
    issues: [...new Set(issues)],
  } as PreparedProfileEditorConfig<T>;
}

export function profileConditionManualUpdates<T extends Record<string, unknown>>(
  updates: T,
  showPoints: boolean,
): T & { rule_id?: undefined; points?: number; category?: undefined } {
  if (!showPoints) return { ...updates };
  return {
    ...updates,
    rule_id: undefined,
    points: 0,
    category: undefined,
  };
}

export function serializeProfileRuleCondition<T extends Record<string, unknown>>(condition: T): T {
  const serialized = { ...condition };
  if (
    typeof serialized.id === "string"
    && serialized.id.startsWith("cond_loaded_")
  ) {
    delete serialized.id;
  }
  return serialized;
}

/** Remove identities created only so React can address legacy list items. */
export function serializeProfileEditorConfig<T extends Record<string, any>>(config: T): T {
  const serializeConditions = (conditions: Array<Record<string, unknown>> = []) => (
    conditions.map((condition) => serializeProfileRuleCondition(condition))
  );
  const serialized = {
    ...config,
    filters: {
      ...(config.filters || {}),
      conditions: serializeConditions(config.filters?.conditions),
    },
    signals: {
      ...(config.signals || {}),
      conditions: serializeConditions(config.signals?.conditions),
    },
    block_rules: {
      ...(config.block_rules || {}),
      blocks: (config.block_rules?.blocks || []).map((block: Record<string, any>) => {
        const serializedBlock: Record<string, any> = {
          ...block,
          conditions: serializeConditions(block.conditions),
        };
        if (
          typeof serializedBlock.id === "string"
          && serializedBlock.id.startsWith("block_loaded_")
        ) {
          delete serializedBlock.id;
        }
        return serializedBlock;
      }),
    },
    entry_triggers: {
      ...(config.entry_triggers || {}),
      conditions: serializeConditions(config.entry_triggers?.conditions),
    },
  };
  return serialized as T;
}
