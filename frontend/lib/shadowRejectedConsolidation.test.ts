import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";


test("rejected list renders the primary profile and associated count", () => {
  const page = readFileSync(
    resolve(process.cwd(), "app/dashboard/shadow-portfolio/page.tsx"),
    "utf8",
  );
  assert.match(page, /primary_profile\.profile_name/);
  assert.match(page, /associated_count/);
  assert.match(page, /associados/);
  assert.match(page, /consolidationTooltip/);
});


test("active rejected rows are consolidated by the backend before pagination", () => {
  const page = readFileSync(
    resolve(process.cwd(), "app/dashboard/shadow-portfolio/page.tsx"),
    "utf8",
  );
  assert.match(page, /status: "OPEN"/);
  assert.doesNotMatch(page, /Promise\.all\(\[\s*apiGet<ShadowTradeListResponse>/);
  assert.match(page, /if \(source\) params\.set\("source", source\)/);
});


test("detail surface lists every persisted candidate and reason", () => {
  const detail = readFileSync(
    resolve(process.cwd(), "components/shadow-portfolio/ShadowTradeDetailScreen.tsx"),
    "utf8",
  );
  assert.match(detail, /data\.consolidation\.candidates\.map/);
  assert.match(detail, /Profile principal/);
  assert.match(detail, /Profile associado/);
  assert.match(detail, /rejection_reasons/);
});


test("approved and rejected consolidations have independent settings copy", () => {
  const settings = readFileSync(
    resolve(process.cwd(), "app/settings/strategies/page.tsx"),
    "utf8",
  );
  assert.match(settings, /l3_single_profile_per_symbol_enabled/);
  assert.match(settings, /l3_rejected_single_profile_per_symbol_enabled/);
  assert.match(settings, /Esta ativação é independente/);
});
