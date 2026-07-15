export type WindowEvidence = "exact" | "boundary" | "missing";

export interface AuditWindow {
  from: string | null;
  to: string | null;
  evidence: WindowEvidence;
}

export interface ModelDatasetAuditInput {
  hyperparams: Record<string, unknown> | null;
  train_samples: number | null;
  val_samples: number | null;
  test_samples: number | null;
  train_from: string | null;
  train_to: string | null;
  dataset_query_cutoff: string | null;
}

export interface ReconciliationRow {
  key: string;
  label: string;
  count: number | null;
}

export interface ModelDatasetAudit {
  datasetWindow: AuditWindow;
  trainWindow: AuditWindow;
  validationWindow: AuditWindow;
  testWindow: AuditWindow;
  cutoff: string | null;
  includedTradeCount: number | null;
  featureRejectedCount: number | null;
  datasetRows: number | null;
  reconciliationRows: ReconciliationRow[];
  reconciledTotal: number | null;
  reconciles: boolean | null;
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function asCount(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) && value >= 0
    ? Math.trunc(value)
    : null;
}

function asTimestamp(value: unknown): string | null {
  return typeof value === "string" && value.length > 0 ? value : null;
}

function completeSum(values: Array<number | null>): number | null {
  return values.every((value) => value != null)
    ? values.reduce<number>((total, value) => total + (value ?? 0), 0)
    : null;
}

export function buildModelDatasetAudit(model: ModelDatasetAuditInput): ModelDatasetAudit {
  const hyperparams = asRecord(model.hyperparams);
  const split = asRecord(hyperparams.split_diagnostics);

  const rawTrain = asCount(split.raw_train);
  const rawValidation = asCount(split.raw_validation);
  const rawTest = asCount(split.raw_test);
  const embargoedTest = asCount(split.embargoed_test);
  const derivedDatasetRows = completeSum([
    rawTrain,
    rawValidation,
    rawTest,
    embargoedTest,
  ]);
  const datasetRows = asCount(split.dataset_rows) ?? derivedDatasetRows;
  const includedTradeCount = asCount(hyperparams.included_trade_count);
  const featureRejectedCount = (
    includedTradeCount != null
    && datasetRows != null
    && includedTradeCount >= datasetRows
  ) ? includedTradeCount - datasetRows : null;

  const train = model.train_samples ?? asCount(split.effective_train_samples);
  const validation = model.val_samples ?? asCount(split.effective_validation_samples);
  const test = model.test_samples ?? asCount(split.effective_test_samples);
  const purgedTrain = asCount(split.purged_train);
  const purgedValidation = asCount(split.purged_validation);
  const reconciledTotal = completeSum([
    train,
    purgedTrain,
    validation,
    purgedValidation,
    embargoedTest,
    test,
  ]);

  const datasetFrom = asTimestamp(split.dataset_from);
  const datasetTo = asTimestamp(split.dataset_to);
  const validationFrom = asTimestamp(split.validation_from);
  const validationTo = asTimestamp(split.validation_to);
  const testFrom = asTimestamp(split.test_from);
  const testTo = asTimestamp(split.test_to);
  const validationBoundary = asTimestamp(split.validation_boundary);
  const testBoundary = asTimestamp(split.test_boundary);
  const testEffectiveStart = asTimestamp(split.test_effective_start);

  return {
    datasetWindow: {
      from: datasetFrom,
      to: datasetTo,
      evidence: datasetFrom && datasetTo ? "exact" : "missing",
    },
    trainWindow: {
      from: asTimestamp(split.train_from) ?? model.train_from,
      to: asTimestamp(split.train_to) ?? model.train_to,
      evidence: (model.train_from || asTimestamp(split.train_from)) ? "exact" : "missing",
    },
    validationWindow: validationFrom && validationTo
      ? { from: validationFrom, to: validationTo, evidence: "exact" }
      : validationBoundary && testBoundary
        ? { from: validationBoundary, to: testBoundary, evidence: "boundary" }
        : { from: null, to: null, evidence: "missing" },
    testWindow: testFrom && testTo
      ? { from: testFrom, to: testTo, evidence: "exact" }
      : testEffectiveStart
        ? { from: testEffectiveStart, to: null, evidence: "boundary" }
        : { from: null, to: null, evidence: "missing" },
    cutoff: model.dataset_query_cutoff,
    includedTradeCount,
    featureRejectedCount,
    datasetRows,
    reconciliationRows: [
      { key: "train", label: "Treino efetivo", count: train },
      { key: "purged-train", label: "Purge do treino", count: purgedTrain },
      { key: "validation", label: "Validação efetiva", count: validation },
      { key: "purged-validation", label: "Purge da validação", count: purgedValidation },
      { key: "embargoed-test", label: "Embargo antes do teste", count: embargoedTest },
      { key: "test", label: "Teste efetivo", count: test },
    ],
    reconciledTotal,
    reconciles: datasetRows != null && reconciledTotal != null
      ? datasetRows === reconciledTotal
      : null,
  };
}
