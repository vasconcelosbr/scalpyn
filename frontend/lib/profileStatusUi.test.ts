import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const builder = readFileSync(
  new URL("../components/profiles/ProfileBuilder.tsx", import.meta.url),
  "utf8",
);

test("profile status is changed only through the dedicated endpoints", () => {
  const savePayload = builder.slice(
    builder.indexOf("const profileData = {"),
    builder.indexOf("onSave(profileData)"),
  );
  assert.equal(savePayload.includes("is_active"), false);
  assert.match(builder, /status-preview/);
  assert.match(builder, /apiPatch<ProfileStatusResult>\(`\/profiles\/\$\{profile\.id\}\/status`/);
});

test("status UI exposes reason, impact, persistent state and safe blocking", () => {
  assert.match(builder, /profile-status-badge/);
  assert.match(builder, /Inativar profile/);
  assert.match(builder, /Reativar profile/);
  assert.match(builder, /Justificativa obrigatória/);
  assert.match(builder, /statusPreview\?\.allowed !== true/);
  assert.match(builder, /Trades e shadows já abertos continuarão sendo acompanhados normalmente/);
});
