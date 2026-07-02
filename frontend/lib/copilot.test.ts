import assert from "node:assert/strict";
import test from "node:test";

import { APPROVAL_TEXT, formatCell, isApprovalValid } from "./copilot";

test("approval requires an explicit canonical phrase", () => {
  assert.equal(isApprovalValid(APPROVAL_TEXT), true);
  assert.equal(isApprovalValid("  confirmo   executar "), true);
  assert.equal(isApprovalValid("APROVADO, EXECUTAR"), true);
  assert.equal(isApprovalValid("aprovar"), false);
});

test("evidence cells preserve structured values", () => {
  assert.equal(formatCell(null), "—");
  assert.equal(formatCell({ sample: 30 }), '{"sample":30}');
  assert.equal(formatCell(0.42), "0.42");
});