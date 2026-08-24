export interface RuleIndicatorValue {
  value: string;
}

export interface RuleConditionValue {
  type?: string;
  indicator?: string;
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
