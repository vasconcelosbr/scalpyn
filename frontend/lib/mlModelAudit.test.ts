import assert from "node:assert/strict";
import test from "node:test";

import { buildModelDatasetAudit } from "./mlModelAudit";

test("reconciles legacy L3 split without inventing a dataset window", () => {
  const audit = buildModelDatasetAudit({
    hyperparams: {
      included_trade_count: 499,
      split_diagnostics: {
        raw_train: 285,
        raw_validation: 77,
        raw_test: 56,
        purged_train: 34,
        purged_validation: 45,
        embargoed_test: 80,
        validation_boundary: "2026-07-14T00:36:43Z",
        test_boundary: "2026-07-14T01:39:10Z",
        test_effective_start: "2026-07-14T05:39:10Z",
      },
    },
    train_samples: 251,
    val_samples: 32,
    test_samples: 56,
    train_from: "2026-07-13T04:42:40Z",
    train_to: "2026-07-14T00:13:54Z",
    dataset_query_cutoff: "2026-07-14T12:41:53Z",
  });

  assert.equal(audit.datasetRows, 498);
  assert.equal(audit.featureRejectedCount, 1);
  assert.equal(audit.reconciledTotal, 498);
  assert.equal(audit.reconciles, true);
  assert.equal(audit.datasetWindow.evidence, "missing");
  assert.equal(audit.trainWindow.evidence, "exact");
  assert.equal(audit.validationWindow.evidence, "boundary");
  assert.equal(audit.testWindow.evidence, "boundary");
});

test("uses exact persisted windows for new models", () => {
  const audit = buildModelDatasetAudit({
    hyperparams: {
      included_trade_count: 120,
      split_diagnostics: {
        dataset_rows: 118,
        dataset_from: "2026-07-01T00:00:00Z",
        dataset_to: "2026-07-03T00:00:00Z",
        train_from: "2026-07-01T00:00:00Z",
        train_to: "2026-07-01T20:00:00Z",
        validation_from: "2026-07-02T00:00:00Z",
        validation_to: "2026-07-02T08:00:00Z",
        test_from: "2026-07-02T12:00:00Z",
        test_to: "2026-07-03T00:00:00Z",
        purged_train: 8,
        purged_validation: 5,
        embargoed_test: 10,
      },
    },
    train_samples: 60,
    val_samples: 15,
    test_samples: 20,
    train_from: null,
    train_to: null,
    dataset_query_cutoff: "2026-07-03T04:00:00Z",
  });

  assert.equal(audit.datasetWindow.evidence, "exact");
  assert.equal(audit.validationWindow.evidence, "exact");
  assert.equal(audit.testWindow.evidence, "exact");
  assert.equal(audit.datasetRows, 118);
  assert.equal(audit.reconciledTotal, 118);
  assert.equal(audit.reconciles, true);
});
