import assert from "node:assert/strict";
import test from "node:test";
import { formatPumpTime, pumpTimingDisplay } from "./pump-monitor-timing";

test("UTC timestamps cross the day boundary in Brasilia regardless of viewer timezone", () => {
  assert.equal(formatPumpTime("2026-11-22T02:30:01+00:00"), "21/11/2026 23:30:01");
  assert.equal(formatPumpTime("2026-11-22T03:00:00Z"), "22/11/2026 00:00:00");
  assert.equal(formatPumpTime("2026-11-22T00:00:00-03:00"), "22/11/2026 00:00:00");
});

test("missing, invalid and timezone-free values never turn into invented times", () => {
  for (const value of [null, undefined, "invalid", "2026-11-22T02:30:00", "invalidZ"]) {
    assert.equal(formatPumpTime(value), null);
  }
  assert.equal(pumpTimingDisplay({}).entry, "Indisponível");
  assert.equal(pumpTimingDisplay({}).strong, "Indisponível");
});

test("initial baseline is labelled as recording start, never historical entry", () => {
  const display = pumpTimingDisplay({timing_status: "available", timing_initial_baseline: true,
    tracking_started_at: "2026-11-22T12:00:00Z", monitor_entered_at: null});
  assert.equal(display.entryLabel, "Registro iniciado em");
  assert.equal(display.entry, "22/11/2026 09:00:00");
  assert.equal(display.strong, "Indisponível");
});

test("normal membership and last strong entry are independently displayed", () => {
  const display = pumpTimingDisplay({timing_status: "available", timing_initial_baseline: false,
    monitor_entered_at: "2026-11-21T12:00:00Z", last_strong_long_at: "2026-11-22T15:30:00Z"});
  assert.equal(display.entryLabel, "No monitor desde");
  assert.equal(display.entry, "21/11/2026 09:00:00");
  assert.equal(display.strong, "22/11/2026 12:30:00");
});

test("storage unavailability does not present stale values as valid", () => {
  const display = pumpTimingDisplay({timing_status: "unavailable", timing_initial_baseline: true,
    monitor_entered_at: "2026-11-22T12:00:00Z", last_strong_long_at: "2026-11-22T12:00:00Z"});
  assert.equal(display.entry, "Indisponível");
  assert.equal(display.strong, "Indisponível");
});
