import assert from "node:assert/strict";
import test from "node:test";

import {
  blockThresholdIndicatorOptions,
  hasCurrentPriceThreshold,
  isCurrentPriceThreshold,
} from "./profileRuleSemantics";

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
