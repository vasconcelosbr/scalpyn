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

test("detailed reports cannot start an Intelligence Run from the paginated trade list", () => {
  assert.match(page, /mainTab !== "detailed-report" && \(\s*<ModuleAIAnalysisAction/);
  assert.match(detailedReport, /reportRunId=\{run\.id\}/);
  assert.match(detailedReport, /Análise por IA \(\$\{run\.total_trades\} trades\)/);
});

test("detailed report AI actions fail closed on incomplete canonical data", () => {
  assert.match(detailedReport, /canonical_analysis_ready !== false/);
  assert.match(detailedReport, /disabled=\{analysisBusy \|\| !canonicalAnalysisReady\}/);
  assert.match(detailedReport, /\{canonicalAnalysisReady && \(/);
  assert.match(detailedReport, /Análise bloqueada antes do provedor/);
});
