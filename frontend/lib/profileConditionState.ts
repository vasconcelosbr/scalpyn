import { STRATEGY_PROFILE_INDICATOR_MAP } from "./indicatorCatalog";

export type ProfileRuleConditionType = "threshold" | "boolean" | "comparison";
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
