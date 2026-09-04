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

  return {
    ...raw,
    id: String(raw.id || fallbackId),
    type: inferredType,
    indicator,
    operator: String(raw.operator || (inferredType === "boolean" ? "is_true" : "<")),
    value: inferredType === "boolean"
      ? raw.value !== undefined ? raw.value : raw.operator === "is_false" ? false : true
      : raw.value !== undefined ? raw.value : raw.operator === "between" ? undefined : 60,
  };
}

export function updateProfileRuleCondition<T extends Record<string, unknown>>(
  condition: T,
  updates: Partial<T>,
): T {
  return { ...condition, ...updates };
}

export function serializeProfileRuleCondition<T extends Record<string, unknown>>(condition: T): T {
  return { ...condition };
}
