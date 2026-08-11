import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const page = readFileSync(new URL("../app/intelligence-runs/page.tsx", import.meta.url), "utf8");

test("completed intelligence runs expose their persisted analysis in the UI", () => {
  assert.match(page, /Resultado da análise/);
  assert.match(page, /root_cause_classification/);
  assert.match(page, /analysis\.diagnosis/);
  assert.match(page, /Evidências usadas/);
  assert.match(page, /result\.recommendations/);
  assert.match(page, /result\.warnings/);
  assert.match(page, /result\.limitations/);
});
