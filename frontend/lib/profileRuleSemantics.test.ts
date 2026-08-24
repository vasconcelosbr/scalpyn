import assert from "node:assert/strict";
import test from "node:test";

import {
  blockThresholdIndicatorOptions,
  COMPARISON_OPERATORS,
  hasCurrentPriceThreshold,
  hasInvalidBetweenBounds,
  isCurrentPriceThreshold,
} from "./profileRuleSemantics";

test("Comparison exposes between for explicit Min and Max bounds", () => {
  assert.equal(COMPARISON_OPERATORS.includes("between"), true);
});

test("Price is excluded from Block Rule threshold options", () => {
  const options = blockThresholdIndicatorOptions([
    { value: "price", label: "Price" },
    { value: "ema21_distance_pct", label: "EMA 21 Distance %" },
  ]);

  assert.deepEqual(options.map((option) => option.value), ["ema21_distance_pct"]);
});

test("legacy Price thresholds are detected before profile persistence", () => {
  const condition = { type: "threshold", indicator: "price" };

  assert.equal(isCurrentPriceThreshold(condition), true);
  assert.equal(hasCurrentPriceThreshold([{ conditions: [condition] }]), true);
  assert.equal(hasCurrentPriceThreshold([
    { conditions: [{ type: "comparison", indicator: "price" }] },
  ]), false);
});

test("between requires explicit ordered Min and Max values", () => {
  assert.equal(hasInvalidBetweenBounds({ operator: "between" }), true);
  assert.equal(hasInvalidBetweenBounds({ operator: "between", min: null, max: 1 }), true);
  assert.equal(hasInvalidBetweenBounds({ operator: "between", min: -7.25, max: 13.5 }), false);
  assert.equal(hasInvalidBetweenBounds({ operator: "between", min: 0, max: 0 }), false);
  assert.equal(hasInvalidBetweenBounds({ operator: "between", min: 2, max: 1 }), true);
  assert.equal(hasInvalidBetweenBounds({ operator: ">=", value: 0 }), false);
});
