import assert from "node:assert/strict";
import test from "node:test";

import { chartTickMarkFormatter, chartTimeFormatter, formatDate, formatDateTime, formatTime } from "./datetime";

const UTC_2025_09_10_20_00_00 = "2025-09-10T20:00:00Z"; // 17:00:00 in GMT-3

test("formatDateTime renders America/Sao_Paulo wall-clock, not the process timezone", () => {
  const out = formatDateTime(UTC_2025_09_10_20_00_00, { hour: "2-digit", minute: "2-digit", hour12: false });
  assert.match(out, /17:00/);
});

test("formatDate/formatTime split correctly across the GMT-3 offset", () => {
  // 2025-09-11 01:30 UTC = 2025-09-10 22:30 GMT-3 — still the previous calendar day.
  const beforeMidnightBrazil = "2025-09-11T01:30:00Z";
  assert.match(formatDate(beforeMidnightBrazil), /10\/09\/2025/);
  assert.match(formatTime(beforeMidnightBrazil, { hour12: false }), /22:30/);
});

test("chart formatters treat their numeric input as epoch seconds, not milliseconds", () => {
  const epochSeconds = Date.parse(UTC_2025_09_10_20_00_00) / 1000;
  assert.match(chartTimeFormatter(epochSeconds), /17:00/);
  assert.match(chartTickMarkFormatter(epochSeconds), /17:00/);
});
