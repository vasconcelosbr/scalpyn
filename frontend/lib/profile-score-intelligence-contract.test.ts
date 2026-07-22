import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { resolve } from "node:path";

const panel = readFileSync(resolve(process.cwd(), "app/profile-intelligence/ScoreIntelligencePanel.tsx"), "utf8");
const page = readFileSync(resolve(process.cwd(), "app/profile-intelligence/page.tsx"), "utf8");
const manual = readFileSync(resolve(process.cwd(), "app/profile-intelligence/ManualAdjustmentPanel.tsx"), "utf8");

test("Score Intelligence exposes card, expanded view, filters, simulator and states", () => {
  for (const token of [
    "Score Intelligence — TP × SL", "TP × SL × TIMEOUT", "Distribuição por faixas",
    "Simulador read-only", "Comparação por versão", "Evidência técnica",
    "LOADING", "EMPTY", "INSUFFICIENT_SAMPLE", "ERROR",
  ]) assert.ok(panel.includes(token), token);
  assert.ok(page.includes('"Score Intelligence"'));
  assert.ok(page.includes("ScoreIntelligenceOverviewCard"));
});

test("frontend uses only analytics endpoints and keeps manual flow explicit", () => {
  for (const endpoint of [
    "/score-intelligence/overview", "/score-intelligence/distribution",
    "/score-intelligence/version-comparison", "/score-intelligence/simulate-threshold",
  ]) assert.ok(panel.includes(endpoint), endpoint);
  assert.ok(panel.includes("Criar ajuste manual"));
  assert.ok(panel.includes("OBSERVE_ONLY"));
  assert.ok(page.includes("prefill={scoreManualDraft.prefill}"));
  assert.ok(manual.includes("indicator_stat_id:"));
});

test("UI labels point-in-time, non-causality, missing coverage and ML isolation", () => {
  for (const token of [
    "sem recálculo histórico", "Associação observacional", "Null permanece ausente",
    "Point-in-time: preservado", "ML mutation: nenhuma", "não persistidos no ML",
  ]) assert.ok(panel.includes(token), token);
});
