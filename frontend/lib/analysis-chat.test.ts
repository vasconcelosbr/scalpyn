import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const panel = readFileSync(new URL("../components/ai/AnalysisChatPanel.tsx", import.meta.url), "utf8");
const page = readFileSync(new URL("../app/intelligence-runs/page.tsx", import.meta.url), "utf8");

test("chat button is gated by completed run and persisted result", () => {
  assert.match(page, /selected\?\.status === "COMPLETED"/);
  assert.match(page, /runContext\?\.result\.status === "COMPLETED"/);
  assert.match(panel, /Conversar sobre esta análise/);
});

test("chat renders model, evidence, budget and provider-blocked state", () => {
  assert.match(panel, /Provider bloqueado/);
  assert.match(panel, /evidence_refs/);
  assert.match(panel, /budget_reservation/);
  assert.match(panel, /effective_provider/);
});

test("chat uses authenticated SSE with reconnect and safe plain-text rendering", () => {
  assert.match(panel, /text\/event-stream/);
  assert.match(panel, /Last-Event-ID/);
  assert.match(panel, /Authorization/);
  assert.doesNotMatch(panel, /dangerouslySetInnerHTML/);
});

test("authority-expanding modes require confirmation and can be cancelled", () => {
  assert.match(panel, /window\.confirm/);
  assert.match(panel, /provider_max_cost_usd/);
  assert.match(panel, /autoriza até US\$/);
  assert.match(panel, /CREATE_CHILD_ANALYSIS/);
  assert.match(panel, /DRAFT_PROPOSAL/);
  assert.match(panel, /\/cancel/);
});
