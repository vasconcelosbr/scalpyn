import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const page = readFileSync(
  new URL("../app/dashboard/shadow-portfolio/page.tsx", import.meta.url),
  "utf8",
);
const detailedReport = readFileSync(
  new URL("../components/shadow-portfolio/DetailedReportWorkspace.tsx", import.meta.url),
  "utf8",
);
const tradeDetail = readFileSync(
  new URL("../components/shadow-portfolio/ShadowTradeDetailScreen.tsx", import.meta.url),
  "utf8",
);

test("shadow analysis entry points require the canonical detailed-report flow", () => {
  assert.doesNotMatch(page, /<ModuleAIAnalysisAction/);
  assert.match(page, /onClick=\{\(\) => setMainTab\("detailed-report"\)\}/);
  assert.match(page, /Preparar análise por IA/);
  assert.doesNotMatch(tradeDetail, /<ModuleAIAnalysisAction/);
  assert.match(tradeDetail, /href="\/dashboard\/shadow-portfolio#detailed-report"/);
  assert.match(detailedReport, /reportRunId=\{run\.id\}/);
  assert.match(detailedReport, /Análise por IA \(\$\{run\.total_trades\} trades\)/);
});

test("detailed report AI actions fail closed on incomplete canonical data", () => {
  assert.match(detailedReport, /canonical_analysis_ready !== false/);
  assert.match(detailedReport, /disabled=\{analysisBusy \|\| !canonicalAnalysisReady\}/);
  assert.match(detailedReport, /\{canonicalAnalysisReady && \(/);
  assert.match(detailedReport, /Análise bloqueada antes do provedor/);
});

test("detailed report outcome controls expose selected state and stale-result guidance", () => {
  assert.match(detailedReport, /aria-pressed=\{selected\}/);
  assert.match(detailedReport, /data-state=\{selected \? "selected" : "unselected"\}/);
  assert.match(detailedReport, /selected \? OUTCOME_ACTIVE_CLASS\[outcome\] : outcomeButtonInactive/);
  assert.match(detailedReport, /toggleShadowReportOutcome\(current, outcome\)/);
  assert.match(detailedReport, /Aplicar filtros e executar relatório/);
  assert.match(detailedReport, /Os resultados abaixo ainda pertencem à execução anterior/);
});
