import assert from "node:assert/strict";
import test from "node:test";

import {
  ALL_PROFILES_VALUE,
  analysisTargets,
  batchIdempotencyKey,
  deduplicateProfileOptions,
} from "./profile-bayesian-batch";

const profiles = [
  { profile_id: "profile-a", profile_name: "Profile A" },
  { profile_id: "profile-a", profile_name: "Profile A / duplicated source" },
  { profile_id: "profile-b", profile_name: "Profile B" },
];

test("deduplicateProfileOptions keeps one target per profile id", () => {
  assert.deepEqual(deduplicateProfileOptions(profiles), [
    profiles[0],
    profiles[2],
  ]);
});

test("analysisTargets expands the all-profiles selection", () => {
  const unique = deduplicateProfileOptions(profiles);
  assert.deepEqual(analysisTargets(ALL_PROFILES_VALUE, unique), unique);
  assert.deepEqual(analysisTargets("profile-b", unique), [unique[1]]);
  assert.deepEqual(analysisTargets("missing", unique), []);
});

test("batch idempotency is stable and scoped by profile", () => {
  assert.equal(
    batchIdempotencyKey("batch-1", "profile-a"),
    "profile-batch:batch-1:profile-a",
  );
  assert.notEqual(
    batchIdempotencyKey("batch-1", "profile-a"),
    batchIdempotencyKey("batch-1", "profile-b"),
  );
});
