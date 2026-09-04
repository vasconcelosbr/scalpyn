import assert from "node:assert/strict";
import test from "node:test";

import { profilesEligibleForSubmission, validateProfileImport } from "./profileImportPreflight";

const base = (): Record<string, any> => ({
  profile_id: "11111111-1111-4111-8111-111111111111",
  expected_profile_version_id: "22222222-2222-4222-8222-222222222222",
  expected_profile_config_hash: "a".repeat(64),
  filters: { logic: "AND", conditions: [] },
  signals: { logic: "AND", conditions: [] },
  block_rules: { blocks: [] },
  entry_triggers: { logic: "AND", conditions: [] },
});

test("accepts all eight canonical indicators in their supported sections", () => {
  const profile = base();
  profile.block_rules.blocks = [{
    id: "b1",
    conditions: [
      "adx_acceleration", "adx_slope_3", "macd_hist_slope_3", "rsi_slope_3",
      "entry_exhaustion_score", "rsi_6", "breakout_distance_pct",
    ].map((indicator) => ({
      type: "threshold", indicator, operator: ">=", value: 0,
      ...(indicator === "rsi_6" ? { period: 6 } : {}),
      ...(indicator === "breakout_distance_pct" ? { reference_window: "15m" } : {}),
    })),
  }];
  profile.entry_triggers.conditions = [{
    type: "threshold", indicator: "macd_hist_slope_5", operator: ">=", value: 0,
    required: true, enabled: true,
  }];
  assert.deepEqual(validateProfileImport(profile, 0, true).issues, []);
});

test("legacy update fixture is blocked by concurrency and breakout contract errors", () => {
  const profile = base();
  delete (profile as Partial<typeof profile>).expected_profile_version_id;
  delete (profile as Partial<typeof profile>).expected_profile_config_hash;
  profile.block_rules.blocks = [{
    conditions: [{ type: "threshold", indicator: "breakout_distance_pct", operator: ">", value: 0.8 }],
  }];
  const result = validateProfileImport(profile, 7, true);
  assert.equal(result.valid, false);
  assert.deepEqual(result.issues.map((value) => value.code), [
    "EXPECTED_PROFILE_VERSION_REQUIRED",
    "EXPECTED_PROFILE_CONFIG_HASH_REQUIRED",
    "REFERENCE_WINDOW_REQUIRED",
  ]);
  assert.equal(result.issues[2].path, "profiles[7].block_rules.blocks[0].conditions[0].reference_window");
});

test("rejects unknown indicator, wrong section, invalid operator and missing value with exact paths", () => {
  const profile = base();
  profile.signals.conditions = [{ field: "adx_acceleration", operator: "contains" }];
  profile.entry_triggers.conditions = [{ indicator: "future_indicator", operator: ">=" }];
  const result = validateProfileImport(profile, 3, true);
  assert.equal(result.valid, false);
  assert.ok(result.issues.some((value) => value.code === "INDICATOR_SECTION_NOT_ALLOWED" && value.path === "profiles[3].signals.conditions[0].field"));
  assert.ok(result.issues.some((value) => value.code === "UNKNOWN_INDICATOR" && value.path === "profiles[3].entry_triggers.conditions[0].indicator"));
  assert.ok(result.issues.some((value) => value.code === "OPERATOR_INVALID"));
  assert.ok(result.issues.some((value) => value.code === "VALUE_REQUIRED"));
});

test("update mode never submits a partial batch", () => {
  const batch = [{ id: "ok", valid: true }, { id: "bad", valid: false }];
  assert.deepEqual(profilesEligibleForSubmission(batch, true), []);
  assert.deepEqual(profilesEligibleForSubmission(batch, false), [batch[0]]);
});
