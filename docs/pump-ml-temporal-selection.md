# Pump challenger temporal sampling proposal

The latest eligible-row cap keeps a short moving tail even as older compatible
history grows. With the existing conservative purge and embargo, validation,
calibration and test can become empty despite both classes existing before
purging. Query optimization alone does not correct that sampling policy.

The proposed `pump_temporal_equal_duration_v1` policy keeps the same maximum
10,000 rows, owner, latest certified contract, 30-day window, numeric features,
complete labels and target. It finds the complete-feature eligible temporal
extent in one repeatable-read snapshot, divides elapsed time into 20 equal
windows and selects up to 500 rows per window. These are provisional mechanical
sampling defaults, not calibrated admission thresholds. Read batch size remains
an independent I/O setting.

Numeric feature eligibility runs before consuming the quota. Within each window,
decision-time descending/UUID ascending ordering is retained. Empty or sparse
windows stay underfilled; there is no newest-tail backfill, class balancing,
outcome-dependent boundary search or score-dependent selection. Policy version,
extent, quotas and actual counts are frozen into the training manifest so this
dataset cannot be mistaken for the previous newest-row selection.

The original split percentiles, purge maximum horizon, embargo, class/support
gates, resource ceilings, daily singleton, model status and promotion safeguards
remain unchanged. Populated temporal cohorts are not proof of calibrated
probabilities or useful prediction: few positive episodes, short history and
limited regimes still require independent review. No live/Shadow consumer changes.

Validation must cover batch-size invariant membership, feature eligibility,
numeric zero, sparse windows, tied timestamps/window edges, class/score
independence, cancellation cleanup, support after purge/embargo and episode
isolation. Read-only production-data comparisons should report distinct snapshot
times and independent positive episodes; they must not claim model validation.
Further resource validation must respect existing statement/job limits.

This policy is prepared locally pending publication/deployment approval. Risks
include reduced emphasis on recent drift and missing short-lived events under
quotas. Endpoint discovery cost grows with history, so retain bounded payload
reads and fail closed under current execution limits. No extra production train
or singleton override is part of this proposal.
