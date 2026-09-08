import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import test from "node:test";

test("MTF waiver is explicit in audit UI and governed import flow", () => {
  const settings = readFileSync(
    resolve(process.cwd(), "app/settings/strategies/page.tsx"),
    "utf8",
  );
  const importer = readFileSync(
    resolve(process.cwd(), "components/profiles/JsonImportBuilder.tsx"),
    "utf8",
  );

  assert.match(settings, /ESTATISTICAMENTE NÃO APROVADO/);
  assert.match(settings, /thresholds permanecem não validados/);
  assert.match(settings, /não pode autorizar ordens/);
  assert.match(settings, /Contextos completos desde o contrato atual.*v5_complete_contexts_current_contract/);
  assert.match(
    importer,
    /UPDATE_EXISTING_MTF_AND_ACTIVATE_SHADOW_WITH_WAIVER/,
  );
  assert.match(importer, /Confirmar SHADOW NÃO CALIBRADO/);
});
