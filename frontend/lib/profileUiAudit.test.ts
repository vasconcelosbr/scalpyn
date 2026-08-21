import assert from "node:assert/strict";
import test from "node:test";

import { buildProfileUiAudit, buildProfilesUiAudit, resolveProfileUiIndicator } from "./profileUiAudit";

function configWithIndicator(indicator: string) {
  return {
    default_timeframe: "5m",
    filters: { logic: "AND", conditions: [] },
    signals: { logic: "AND", conditions: [] },
    block_rules: {
      blocks: [{
        name: "Audit block",
        enabled: true,
        logic: "AND",
        timeframe: "5m",
        conditions: [{ type: "threshold", indicator, operator: "<=", value: 0 }],
      }],
    },
    entry_triggers: { logic: "AND", conditions: [] },
  };
}

function audit(backendIndicator: string, formIndicator = backendIndicator, saveIndicator = formIndicator) {
  const backendConfig = configWithIndicator(backendIndicator);
  const formConfig = configWithIndicator(formIndicator);
  const saveConfig = configWithIndicator(saveIndicator);
  return buildProfileUiAudit({
    profile: { id: "profile-1", name: "L3_TEST" },
    backendConfig,
    formConfig,
    savePayload: { name: "L3_TEST", config: saveConfig },
    exportedAt: "2026-08-21T00:00:00.000Z",
  });
}

test("known indicator resolves to its registered value and label", () => {
  const resolved = resolveProfileUiIndicator(
    "block_rules",
    { type: "threshold" },
    "stoch_k",
  );
  assert.deepEqual(resolved, {
    requested_indicator_value: "stoch_k",
    indicator_value: "stoch_k",
    indicator_label: "Stoch %K",
    rendered_option_value: "stoch_k",
    registry_found: true,
  });
});

for (const indicator of [
  "adx_slope_3",
  "adx_acceleration",
  "entry_exhaustion_score",
  "rsi_slope_3",
  "macd_hist_slope_3",
]) {
  test(`${indicator} is preserved through form and save while the Price fallback is audited`, () => {
    const result = audit(indicator);
    const row = result.profiles[0].round_trip_audit[0];
    assert.equal(row.diff.backend_indicator, indicator);
    assert.equal(row.diff.form_indicator, indicator);
    assert.equal(row.diff.save_indicator, indicator);
    assert.equal(row.ui.requested_indicator_value, indicator);
    assert.equal(row.ui.indicator_value, "price");
    assert.equal(row.ui.indicator_label, "Price");
    assert.equal(row.ui.rendered_option_value, "price");
    assert.equal(row.round_trip_ok, false);
    assert.ok(row.codes.includes("UNKNOWN_INDICATOR"));
    assert.ok(row.codes.includes("INDICATOR_FALLBACK_TO_PRICE"));
  });
}

test("unknown future indicator is never silently serialized as price", () => {
  const result = audit("future_test_indicator");
  const row = result.profiles[0].round_trip_audit[0];
  assert.equal(row.diff.form_indicator, "future_test_indicator");
  assert.equal(row.diff.save_indicator, "future_test_indicator");
  assert.equal(row.ui.requested_indicator_value, "future_test_indicator");
  assert.equal(row.ui.indicator_value, "price");
  assert.equal(row.ui.registry_found, false);
  assert.ok(row.codes.includes("UNKNOWN_INDICATOR"));
  assert.equal(result.summary.fallback_to_price_detected, 1);
});

test("indicator identity change during deserialize is critical", () => {
  const result = audit("adx_slope_3", "price", "price");
  const row = result.profiles[0].round_trip_audit[0];
  assert.equal(row.severity, "CRITICAL");
  assert.ok(row.codes.includes("INDICATOR_CHANGED_DURING_DESERIALIZE"));
  assert.ok(row.codes.includes("INDICATOR_FALLBACK_TO_PRICE"));
});

test("indicator identity change during serialize is critical", () => {
  const result = audit("stoch_k", "stoch_k", "price");
  const row = result.profiles[0].round_trip_audit[0];
  assert.equal(row.severity, "CRITICAL");
  assert.ok(row.codes.includes("INDICATOR_CHANGED_DURING_SERIALIZE"));
});

test("UI rendered config mirrors the visible Price fallback without changing form or save state", () => {
  const result = audit("adx_slope_3");
  const profile = result.profiles[0];
  type ConfigWithBlock = {
    block_rules: { blocks: Array<{ conditions: Array<{ indicator: string }> }> };
  };
  const renderedConfig = profile.ui_rendered_config as ConfigWithBlock;
  const formConfig = profile.form_state as ConfigWithBlock;
  const saveConfig = profile.save_payload.config as ConfigWithBlock;
  const renderedCondition = renderedConfig.block_rules.blocks[0].conditions[0];
  const formCondition = formConfig.block_rules.blocks[0].conditions[0];
  const saveCondition = saveConfig.block_rules.blocks[0].conditions[0];

  assert.equal(result.schema_version, 2);
  assert.equal(profile.ui_rendered_config_metadata.audit_only, true);
  assert.equal(profile.ui_rendered_config_metadata.safe_to_import, false);
  assert.equal(renderedCondition.indicator, "price");
  assert.equal(formCondition.indicator, "adx_slope_3");
  assert.equal(saveCondition.indicator, "adx_slope_3");
});

test("batch UI audit aggregates only the selected profiles", () => {
  const first = audit("adx_slope_3");
  const second = buildProfileUiAudit({
    profile: { id: "profile-2", name: "L3_SECOND" },
    backendConfig: configWithIndicator("stoch_k"),
    formConfig: configWithIndicator("stoch_k"),
    savePayload: { name: "L3_SECOND", config: configWithIndicator("stoch_k") },
    exportedAt: "2026-08-21T00:00:00.000Z",
  });

  const result = buildProfilesUiAudit([first, second], "2026-08-21T00:00:00.000Z");

  assert.equal(result.source, "frontend_batch_ui_render_model");
  assert.deepEqual(result.selection.selected_profile_ids, ["profile-1", "profile-2"]);
  assert.equal(result.summary.profiles_loaded, 2);
  assert.equal(result.summary.profiles_with_differences, 1);
  assert.equal(result.summary.critical_differences, 1);
  assert.equal(result.summary.fallback_to_price_detected, 1);
  assert.equal(result.profiles.length, 2);
});
