import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import test from "node:test";

test("MTF profiles use the manual governed editor instead of a hard block", () => {
  const builder = readFileSync(
    resolve(process.cwd(), "components/profiles/ProfileBuilder.tsx"),
    "utf8",
  );

  assert.doesNotMatch(
    builder,
    /Profiles MTF só podem ser alterados pelo fluxo governado/,
  );
  assert.match(builder, /expected_profile_version_id/);
  assert.match(builder, /expected_profile_config_hash/);
  assert.match(builder, /mtf-governed-editor-notice/);
  assert.match(builder, /nova versão imutável Shadow/);
});

test("profiles API returns the concurrency contract required by the editor", () => {
  const profilesApi = readFileSync(
    resolve(process.cwd(), "../backend/app/api/profiles.py"),
    "utf8",
  );

  assert.match(profilesApi, /_profile_to_dict_with_update_contract/);
  assert.match(profilesApi, /MTF_PROFILE_EDITOR_CONTRACT_REQUIRED/);
  assert.match(profilesApi, /expected_profile_version_id=expected_profile_version_id/);
  assert.match(profilesApi, /change_source="profile_ui_editor"/);
});
