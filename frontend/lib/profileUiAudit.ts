export type IndicatorKind = "number" | "boolean" | "string";

export interface ProfileRuleIndicatorOption {
  value: string;
  label: string;
  kind: IndicatorKind;
}

export interface ProfileConditionIndicatorOption {
  value: string;
  label: string;
  type: IndicatorKind;
  group: string;
}

export const PROFILE_RULE_INDICATORS: ProfileRuleIndicatorOption[] = [
  { value: "price", label: "Price", kind: "number" },
  { value: "ema5", label: "EMA 5", kind: "number" },
  { value: "ema9", label: "EMA 9", kind: "number" },
  { value: "ema21", label: "EMA 21", kind: "number" },
  { value: "ema50", label: "EMA 50", kind: "number" },
  { value: "ema200", label: "EMA 200", kind: "number" },
  { value: "alpha_score", label: "Alpha Score", kind: "number" },
  { value: "rsi", label: "RSI", kind: "number" },
  { value: "adx", label: "ADX", kind: "number" },
  { value: "macd", label: "MACD", kind: "number" },
  { value: "macd_histogram", label: "MACD Histogram", kind: "number" },
  { value: "volume_spike", label: "Volume Spike", kind: "number" },
  { value: "taker_ratio", label: "Taker Ratio (buy/(buy+sell), 0-1)", kind: "number" },
  { value: "volume_delta", label: "Volume Delta", kind: "number" },
  { value: "orderbook_pressure", label: "Orderbook Pressure", kind: "number" },
  { value: "bid_ask_imbalance", label: "Bid/Ask Imbalance", kind: "number" },
  { value: "atr_percent", label: "ATR %", kind: "number" },
  { value: "bb_width", label: "BB Width", kind: "number" },
  { value: "spread_pct", label: "Spread %", kind: "number" },
  { value: "zscore", label: "Z-Score", kind: "number" },
  { value: "funding_rate", label: "Funding Rate", kind: "number" },
  { value: "volume_24h", label: "Volume 24h", kind: "number" },
  { value: "stoch_k", label: "Stoch %K", kind: "number" },
  { value: "stoch_d", label: "Stoch %D", kind: "number" },
  { value: "di_plus", label: "DI+", kind: "number" },
  { value: "di_minus", label: "DI-", kind: "number" },
  { value: "ema_full_alignment", label: "EMA Full Alignment", kind: "boolean" },
  { value: "ema9_gt_ema21", label: "EMA9 > EMA21", kind: "boolean" },
  { value: "ema9_gt_ema50", label: "EMA9 > EMA50", kind: "boolean" },
  { value: "ema50_gt_ema200", label: "EMA50 > EMA200", kind: "boolean" },
  { value: "market_cap", label: "Market Cap", kind: "number" },
  { value: "change_24h", label: "Variacao 24h %", kind: "number" },
  { value: "orderbook_depth_usdt", label: "Profundidade Book (USDT)", kind: "number" },
  { value: "obv", label: "OBV", kind: "number" },
  { value: "vwap_distance_pct", label: "VWAP Distance %", kind: "number" },
  { value: "macd_signal", label: "MACD Signal", kind: "string" },
  { value: "di_trend", label: "DI+ > DI- (Alta)", kind: "boolean" },
  { value: "atr", label: "ATR", kind: "number" },
  { value: "psar_trend", label: "PSAR Trend", kind: "string" },
];

export const PROFILE_CONDITION_INDICATORS: ProfileConditionIndicatorOption[] = [
  { value: "volume_24h", label: "Volume 24h", type: "number", group: "price" },
  { value: "market_cap", label: "Market Cap", type: "number", group: "price" },
  { value: "price", label: "Preco", type: "number", group: "price" },
  { value: "change_24h", label: "Variacao 24h %", type: "number", group: "price" },
  { value: "spread_pct", label: "Spread %", type: "number", group: "liquidity" },
  { value: "orderbook_depth_usdt", label: "Profundidade Book (USDT)", type: "number", group: "liquidity" },
  { value: "taker_ratio", label: "Taker Ratio (buy/(buy+sell), 0-1)", type: "number", group: "liquidity" },
  { value: "volume_spike", label: "Volume Spike", type: "number", group: "liquidity" },
  { value: "volume_delta", label: "Volume Delta", type: "number", group: "liquidity" },
  { value: "orderbook_pressure", label: "Orderbook Pressure", type: "number", group: "liquidity" },
  { value: "bid_ask_imbalance", label: "Bid/Ask Imbalance", type: "number", group: "liquidity" },
  { value: "obv", label: "OBV", type: "number", group: "liquidity" },
  { value: "vwap_distance_pct", label: "VWAP Distance %", type: "number", group: "liquidity" },
  { value: "rsi", label: "RSI", type: "number", group: "momentum" },
  { value: "macd", label: "MACD", type: "number", group: "momentum" },
  { value: "macd_histogram", label: "MACD Histogram", type: "number", group: "momentum" },
  { value: "macd_signal", label: "MACD Signal", type: "string", group: "momentum" },
  { value: "stoch_k", label: "Stochastic %K", type: "number", group: "momentum" },
  { value: "stoch_d", label: "Stochastic %D", type: "number", group: "momentum" },
  { value: "zscore", label: "Z-Score", type: "number", group: "momentum" },
  { value: "adx", label: "ADX", type: "number", group: "trend" },
  { value: "di_plus", label: "DI+", type: "number", group: "trend" },
  { value: "di_minus", label: "DI-", type: "number", group: "trend" },
  { value: "di_trend", label: "DI+ > DI- (Alta)", type: "boolean", group: "trend" },
  { value: "atr", label: "ATR", type: "number", group: "trend" },
  { value: "atr_percent", label: "ATR %", type: "number", group: "trend" },
  { value: "bb_width", label: "Bollinger Width", type: "number", group: "trend" },
  { value: "psar_trend", label: "PSAR Trend", type: "string", group: "trend" },
  { value: "ema_full_alignment", label: "EMA Full Alignment", type: "boolean", group: "ema" },
  { value: "ema9_gt_ema21", label: "EMA9 > EMA21", type: "boolean", group: "ema" },
  { value: "ema9_gt_ema50", label: "EMA9 > EMA50", type: "boolean", group: "ema" },
  { value: "ema50_gt_ema200", label: "EMA50 > EMA200", type: "boolean", group: "ema" },
  { value: "score", label: "Alpha Score", type: "number", group: "scores" },
  { value: "liquidity_score", label: "Liquidity Score", type: "number", group: "scores" },
  { value: "momentum_score", label: "Momentum Score", type: "number", group: "scores" },
];

type AuditSection = "filters" | "signals" | "block_rules" | "entry_triggers";
type AuditSeverity = "CRITICAL" | "HIGH" | "MEDIUM" | "LOW";
type AuditRole = "indicator" | "left" | "right";

interface ConditionRef {
  key: string;
  section: AuditSection;
  blockName: string | null;
  conditionIndex: number;
  role: AuditRole;
  condition: Record<string, unknown>;
  indicator: string;
}

interface BuildProfileUiAuditArgs {
  profile: { id?: string; name?: string };
  backendConfig: Record<string, unknown>;
  formConfig: Record<string, unknown>;
  savePayload: Record<string, unknown>;
  trigger?: "manual_export" | "pre_save";
  exportedAt?: string;
}

const ROOTS: AuditSection[] = ["filters", "signals", "block_rules", "entry_triggers"];

function clone<T>(value: T): T {
  return value == null ? value : JSON.parse(JSON.stringify(value));
}

function object(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function array(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value) ? value.map(object) : [];
}

function configRoots(config: Record<string, unknown>) {
  return Object.fromEntries(ROOTS.map((root) => [root, clone(config[root] ?? (root === "block_rules" ? { blocks: [] } : { conditions: [] }))]));
}

function optionsFor(section: AuditSection, condition: Record<string, unknown>) {
  if (section === "filters" || section === "signals") {
    return PROFILE_CONDITION_INDICATORS.map((item) => ({ value: item.value, label: item.label }));
  }
  const kind = condition.type === "boolean" ? "boolean" : "number";
  return PROFILE_RULE_INDICATORS
    .filter((item) => item.kind === kind)
    .map((item) => ({ value: item.value, label: item.label }));
}

export function resolveProfileUiIndicator(
  section: AuditSection,
  condition: Record<string, unknown>,
  indicatorValue: string,
) {
  const options = optionsFor(section, condition);
  const registered = options.find((option) => option.value === indicatorValue);
  const rendered = registered ?? options[0] ?? { value: "", label: "" };
  return {
    requested_indicator_value: indicatorValue,
    indicator_value: rendered.value,
    indicator_label: rendered.label,
    rendered_option_value: rendered.value,
    registry_found: Boolean(registered),
  };
}

function renderConditionConfig(
  section: AuditSection,
  conditionValue: Record<string, unknown>,
) {
  const condition = object(conditionValue);
  const rendered = clone(condition);
  const type = String(condition.type ?? "threshold");

  if (type === "comparison" || (condition.left && condition.right)) {
    rendered.left = resolveProfileUiIndicator(
      section,
      condition,
      String(condition.left ?? ""),
    ).indicator_value;
    rendered.right = resolveProfileUiIndicator(
      section,
      condition,
      String(condition.right ?? ""),
    ).indicator_value;
    return rendered;
  }

  const field = Object.prototype.hasOwnProperty.call(condition, "field")
    ? "field"
    : "indicator";
  rendered[field] = resolveProfileUiIndicator(
    section,
    condition,
    String(condition[field] ?? ""),
  ).indicator_value;
  return rendered;
}

function buildUiRenderedConfig(configValue: Record<string, unknown>) {
  const config = clone(object(configValue));

  for (const section of ["filters", "signals", "entry_triggers"] as const) {
    const sectionConfig = object(config[section]);
    sectionConfig.conditions = array(sectionConfig.conditions).map((condition) =>
      renderConditionConfig(section, condition)
    );
    config[section] = sectionConfig;
  }

  const blockRules = object(config.block_rules);
  blockRules.blocks = array(blockRules.blocks).map((block) => ({
    ...block,
    conditions: array(block.conditions).map((condition) =>
      renderConditionConfig("block_rules", condition)
    ),
  }));
  config.block_rules = blockRules;

  return config;
}

function collectConditionRefs(configValue: Record<string, unknown>): ConditionRef[] {
  const config = object(configValue);
  const refs: ConditionRef[] = [];

  for (const section of ["filters", "signals", "entry_triggers"] as const) {
    array(object(config[section]).conditions).forEach((condition, conditionIndex) => {
      const type = String(condition.type ?? "threshold");
      if (type === "comparison" || (condition.left && condition.right)) {
        for (const role of ["left", "right"] as const) {
          refs.push({
            key: `${section}:${conditionIndex}:${role}`,
            section,
            blockName: null,
            conditionIndex,
            role,
            condition,
            indicator: String(condition[role] ?? ""),
          });
        }
      } else {
        refs.push({
          key: `${section}:${conditionIndex}:indicator`,
          section,
          blockName: null,
          conditionIndex,
          role: "indicator",
          condition,
          indicator: String(condition.field ?? condition.indicator ?? ""),
        });
      }
    });
  }

  array(object(config.block_rules).blocks).forEach((block, blockIndex) => {
    array(block.conditions).forEach((condition, conditionIndex) => {
      const type = String(condition.type ?? "threshold");
      if (type === "comparison" || (condition.left && condition.right)) {
        for (const role of ["left", "right"] as const) {
          refs.push({
            key: `block_rules:${blockIndex}:${conditionIndex}:${role}`,
            section: "block_rules",
            blockName: String(block.name ?? ""),
            conditionIndex,
            role,
            condition,
            indicator: String(condition[role] ?? ""),
          });
        }
      } else {
        refs.push({
          key: `block_rules:${blockIndex}:${conditionIndex}:indicator`,
          section: "block_rules",
          blockName: String(block.name ?? ""),
          conditionIndex,
          role: "indicator",
          condition,
          indicator: String(condition.indicator ?? condition.field ?? ""),
        });
      }
    });
  });

  return refs;
}

function nullable(value: unknown) {
  return value === undefined ? null : value;
}

function uiCondition(section: AuditSection, conditionValue: Record<string, unknown>, defaultTimeframe: string) {
  const condition = object(conditionValue);
  const type = String(condition.type ?? "threshold");
  const base = {
    type,
    operator_value: String(condition.operator ?? ""),
    operator_label: String(condition.operator ?? ""),
    value: nullable(condition.value),
    min: nullable(condition.min),
    max: nullable(condition.max),
    period: nullable(condition.period),
    timeframe_value: String(condition.timeframe ?? defaultTimeframe),
    timeframe_label: String(condition.timeframe ?? defaultTimeframe),
    required: Boolean(condition.required),
    enabled: condition.enabled !== false,
  };
  if (type === "comparison" || (condition.left && condition.right)) {
    return {
      ...base,
      left: resolveProfileUiIndicator(section, condition, String(condition.left ?? "")),
      right: resolveProfileUiIndicator(section, condition, String(condition.right ?? "")),
    };
  }
  return {
    ...base,
    ...resolveProfileUiIndicator(section, condition, String(condition.field ?? condition.indicator ?? "")),
  };
}

function buildUiState(configValue: Record<string, unknown>) {
  const config = object(configValue);
  const defaultTimeframe = String(config.default_timeframe ?? "5m");
  const filters = object(config.filters);
  const signals = object(config.signals);
  const entryTriggers = object(config.entry_triggers);
  const blockRules = object(config.block_rules);
  return {
    default_timeframe: defaultTimeframe,
    filters: {
      logic_value: String(filters.logic ?? "AND"),
      logic_label: String(filters.logic ?? "AND"),
      conditions: array(filters.conditions).map((condition) => uiCondition("filters", condition, defaultTimeframe)),
    },
    signals: {
      logic_value: String(signals.logic ?? "AND"),
      logic_label: String(signals.logic ?? "AND"),
      conditions: array(signals.conditions).map((condition) => uiCondition("signals", condition, defaultTimeframe)),
    },
    block_rules: {
      blocks: array(blockRules.blocks).map((block) => ({
        name: String(block.name ?? ""),
        enabled: block.enabled !== false,
        logic_value: String(block.logic ?? "AND"),
        logic_label: String(block.logic ?? "AND"),
        timeframe_value: String(block.timeframe ?? defaultTimeframe),
        timeframe_label: String(block.timeframe ?? defaultTimeframe),
        reason: String(block.reason ?? ""),
        conditions: array(block.conditions).map((condition) => uiCondition("block_rules", condition, String(block.timeframe ?? defaultTimeframe))),
      })),
    },
    entry_triggers: {
      logic_value: String(entryTriggers.logic ?? "AND"),
      logic_label: String(entryTriggers.logic ?? "AND"),
      conditions: array(entryTriggers.conditions).map((condition) => uiCondition("entry_triggers", condition, defaultTimeframe)),
    },
  };
}

function same(a: unknown, b: unknown) {
  return JSON.stringify(nullable(a)) === JSON.stringify(nullable(b));
}

function changedFields(backend: Record<string, unknown>, current: Record<string, unknown>) {
  return ["operator", "value", "min", "max", "period", "timeframe", "enabled", "required"]
    .filter((field) => !same(backend[field], current[field]));
}

function severityFor(codes: string[], fields: string[]): AuditSeverity | null {
  if (codes.length > 0) return "CRITICAL";
  if (fields.some((field) => ["operator", "value", "min", "max", "period"].includes(field))) return "HIGH";
  if (fields.some((field) => ["timeframe", "enabled", "required"].includes(field))) return "MEDIUM";
  return null;
}

export function buildProfileUiAudit({
  profile,
  backendConfig,
  formConfig,
  savePayload,
  trigger = "manual_export",
  exportedAt = new Date().toISOString(),
}: BuildProfileUiAuditArgs) {
  const backendRefs = new Map(collectConditionRefs(backendConfig).map((ref) => [ref.key, ref]));
  const formRefs = collectConditionRefs(formConfig);
  const saveConfig = object(savePayload.config);
  const saveRefs = new Map(collectConditionRefs(saveConfig).map((ref) => [ref.key, ref]));

  const roundTrip = formRefs.map((formRef) => {
    const backendRef = backendRefs.get(formRef.key);
    const saveRef = saveRefs.get(formRef.key);
    const ui = resolveProfileUiIndicator(formRef.section, formRef.condition, formRef.indicator);
    const codes: string[] = [];
    if (!ui.registry_found) codes.push("UNKNOWN_INDICATOR");
    if (backendRef && backendRef.indicator !== formRef.indicator) codes.push("INDICATOR_CHANGED_DURING_DESERIALIZE");
    if (saveRef && saveRef.indicator !== formRef.indicator) codes.push("INDICATOR_CHANGED_DURING_SERIALIZE");
    if (
      backendRef?.indicator
      && backendRef.indicator !== "price"
      && (ui.indicator_value === "price" || ui.indicator_label === "Price")
    ) {
      codes.push("INDICATOR_FALLBACK_TO_PRICE");
    }
    const fields = backendRef ? changedFields(backendRef.condition, formRef.condition) : [];
    const severity = severityFor(codes, fields);
    return {
      key: formRef.key,
      severity,
      codes,
      section: formRef.section,
      block_name: formRef.blockName,
      condition_index: formRef.conditionIndex,
      indicator_role: formRef.role,
      backend: backendRef ? clone(backendRef.condition) : null,
      form: clone(formRef.condition),
      ui: {
        ...ui,
        operator: nullable(formRef.condition.operator),
        value: nullable(formRef.condition.value),
        min: nullable(formRef.condition.min),
        max: nullable(formRef.condition.max),
        period: nullable(formRef.condition.period),
        timeframe: nullable(formRef.condition.timeframe),
        enabled: formRef.condition.enabled !== false,
        required: Boolean(formRef.condition.required),
      },
      save: saveRef ? clone(saveRef.condition) : null,
      diff: {
        changed_fields: fields,
        backend_indicator: backendRef?.indicator ?? null,
        form_indicator: formRef.indicator,
        ui_indicator_value: ui.indicator_value,
        ui_indicator_label: ui.indicator_label,
        ui_rendered_option_value: ui.rendered_option_value,
        save_indicator: saveRef?.indicator ?? null,
      },
      round_trip_ok: codes.length === 0 && fields.length === 0 && Boolean(saveRef),
    };
  });

  const differences = roundTrip.filter((row) => row.severity !== null);
  const critical = differences.filter((row) => row.severity === "CRITICAL");
  const backendIndicators = [...new Set([...backendRefs.values()].map((ref) => ref.indicator).filter(Boolean))].sort();
  const registeredIndicators = [...new Set([
    ...PROFILE_RULE_INDICATORS.map((item) => item.value),
    ...PROFILE_CONDITION_INDICATORS.map((item) => item.value),
  ])].sort();
  const missingFromSectionRegistry = roundTrip
    .filter((row) => row.codes.includes("UNKNOWN_INDICATOR"))
    .map((row) => ({
      indicator: row.diff.form_indicator,
      section: row.section,
      block_name: row.block_name,
      condition_index: row.condition_index,
    }));
  const unknownIndicators = [...new Set(missingFromSectionRegistry.map((row) => row.indicator))].sort();
  const missingFromFrontendRegistry = unknownIndicators.filter(
    (indicator) => !registeredIndicators.includes(indicator)
  );
  const fallbacks = differences.filter((row) => row.codes.includes("INDICATOR_FALLBACK_TO_PRICE"));

  return {
    export_type: "scalpyn_strategy_profiles_ui_audit",
    schema_version: 2,
    exported_at: exportedAt,
    trigger,
    source: "frontend_runtime_state",
    summary: {
      profiles_loaded: 1,
      profiles_with_differences: differences.length > 0 ? 1 : 0,
      conditions_with_differences: differences.length,
      critical_differences: critical.length,
      unknown_indicators: unknownIndicators.length,
      unknown_indicator_occurrences: missingFromSectionRegistry.length,
      fallback_to_price_detected: fallbacks.length,
    },
    indicator_registry_audit: {
      backend_indicators: backendIndicators,
      frontend_registered_indicators: registeredIndicators,
      missing_from_frontend_registry: missingFromFrontendRegistry,
      missing_from_section_registry: missingFromSectionRegistry,
      unused_frontend_indicators: registeredIndicators.filter((indicator) => !backendIndicators.includes(indicator)),
      fallbacks_detected: fallbacks.map((row) => ({
        severity: "CRITICAL",
        code: "INDICATOR_FALLBACK_TO_PRICE",
        profile: profile.name ?? "",
        section: row.section,
        block: row.block_name,
        condition_index: row.condition_index,
        backend_indicator: row.diff.backend_indicator,
        form_indicator: row.diff.form_indicator,
        ui_indicator_value: row.diff.ui_indicator_value,
        ui_indicator_label: row.diff.ui_indicator_label,
        ui_rendered_option_value: row.diff.ui_rendered_option_value,
      })),
    },
    profiles: [{
      profile_id: profile.id ?? null,
      name: profile.name ?? "",
      backend_state: configRoots(backendConfig),
      form_state: configRoots(formConfig),
      ui_state: buildUiState(formConfig),
      ui_rendered_config_metadata: {
        audit_only: true,
        safe_to_import: false,
        reason: "Esta configuracao reproduz os valores efetivamente renderizados pela UI, inclusive fallbacks incorretos.",
      },
      ui_rendered_config: buildUiRenderedConfig(formConfig),
      save_payload: clone(savePayload),
      ui_backend_diffs: differences,
      round_trip_audit: roundTrip,
    }],
  };
}

type ProfileUiAudit = ReturnType<typeof buildProfileUiAudit>;

export function buildProfilesUiAudit(
  audits: ProfileUiAudit[],
  exportedAt = new Date().toISOString(),
) {
  const profiles = audits.flatMap((audit) => audit.profiles);
  const missingFromSectionRegistry = audits.flatMap(
    (audit) => audit.indicator_registry_audit.missing_from_section_registry,
  );
  const fallbacksDetected = audits.flatMap(
    (audit) => audit.indicator_registry_audit.fallbacks_detected,
  );
  const backendIndicators = [...new Set(audits.flatMap(
    (audit) => audit.indicator_registry_audit.backend_indicators,
  ))].sort();
  const frontendRegisteredIndicators = [...new Set(audits.flatMap(
    (audit) => audit.indicator_registry_audit.frontend_registered_indicators,
  ))].sort();
  const missingFromFrontendRegistry = [...new Set(audits.flatMap(
    (audit) => audit.indicator_registry_audit.missing_from_frontend_registry,
  ))].sort();
  const unknownIndicators = [...new Set(
    missingFromSectionRegistry.map((row) => row.indicator),
  )].sort();
  const uiRenderedProfiles = profiles.map((profile) => {
    const savePayload = object(profile.save_payload);
    return {
      profile_id: profile.profile_id,
      name: profile.name,
      description: nullable(savePayload.description),
      profile_role: nullable(savePayload.profile_role),
      pipeline_order: nullable(savePayload.pipeline_order),
      ...clone(object(profile.ui_rendered_config)),
    };
  });

  return {
    export_type: "scalpyn_strategy_profiles_ui_audit",
    schema_version: 2,
    exported_at: exportedAt,
    trigger: "manual_export" as const,
    source: "frontend_batch_ui_render_model",
    selection: {
      selected_profile_ids: profiles.map((profile) => profile.profile_id),
      selected_profile_names: profiles.map((profile) => profile.name),
    },
    summary: {
      profiles_loaded: profiles.length,
      profiles_with_differences: audits.filter(
        (audit) => audit.summary.profiles_with_differences > 0,
      ).length,
      conditions_with_differences: audits.reduce(
        (total, audit) => total + audit.summary.conditions_with_differences,
        0,
      ),
      critical_differences: audits.reduce(
        (total, audit) => total + audit.summary.critical_differences,
        0,
      ),
      unknown_indicators: unknownIndicators.length,
      unknown_indicator_occurrences: missingFromSectionRegistry.length,
      fallback_to_price_detected: fallbacksDetected.length,
    },
    ui_rendered_profiles_metadata: {
      audit_only: true,
      safe_to_import: false,
      reason: "Esta lista reproduz os valores efetivamente renderizados pela UI, inclusive fallbacks incorretos.",
    },
    ui_rendered_profiles: uiRenderedProfiles,
    indicator_registry_audit: {
      backend_indicators: backendIndicators,
      frontend_registered_indicators: frontendRegisteredIndicators,
      missing_from_frontend_registry: missingFromFrontendRegistry,
      missing_from_section_registry: missingFromSectionRegistry,
      unused_frontend_indicators: frontendRegisteredIndicators.filter(
        (indicator) => !backendIndicators.includes(indicator),
      ),
      fallbacks_detected: fallbacksDetected,
    },
    profiles,
  };
}
