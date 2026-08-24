export interface RuleIndicatorValue {
  value: string;
}

export interface RuleConditionValue {
  type?: string;
  indicator?: string;
  operator?: string;
  min?: number | null;
  max?: number | null;
}

export interface BlockRuleValue {
  conditions?: RuleConditionValue[];
}

export function blockThresholdIndicatorOptions<T extends RuleIndicatorValue>(indicators: T[]): T[] {
  return indicators.filter((indicator) => indicator.value !== "price");
}

export function isCurrentPriceThreshold(condition: RuleConditionValue): boolean {
  return condition.type === "threshold" && condition.indicator === "price";
}

export function hasCurrentPriceThreshold(blocks: BlockRuleValue[] | undefined): boolean {
  return (blocks || []).some((block) =>
    (block.conditions || []).some(isCurrentPriceThreshold),
  );
}

export function hasInvalidBetweenBounds(value: unknown): boolean {
  if (Array.isArray(value)) return value.some(hasInvalidBetweenBounds);
  if (!value || typeof value !== "object") return false;

  const record = value as Record<string, unknown>;
  if (record.operator === "between") {
    const min = record.min;
    const max = record.max;
    if (
      typeof min !== "number" || !Number.isFinite(min) ||
      typeof max !== "number" || !Number.isFinite(max) ||
      min > max
    ) {
      return true;
    }
  }

  return Object.values(record).some(hasInvalidBetweenBounds);
}
