# Pump-only dataset selection timeout correction

The uploaded training service still reports source `0826c295`. Its October 3
run reported `QueryCanceledError`; it never evaluated the temporal training gates.
The same selection SQL in that source was reproduced read-only against the
production database and cancelled at the existing 30-second statement limit.
The plan scans observations with JSON filters, probes labels and sorts before
the final eligible-row limit. The latest certified-contract query also sorts
and examines payloads across the owner history.

`pump_ml.selection` sorts scalar observation IDs once, then reads contract and
label payloads in batches of 500 IDs. It continues past ineligible observations
until the existing limit of 10,000 eligible rows or candidate exhaustion.
Ordering remains decision time descending and observation UUID ascending.
Contract selection is all-time as before, with a deterministic UUID tie break
when certified observations share the newest timestamp. The dataset keeps the
same 30-day lower bound, feature/producer/label contracts, horizon 5, complete
coverage and known boolean outcome requirements. Missing features are still
handled by the original trainer rather than silently replaced or preselected.

Contract and dataset reads share one read-only repeatable-read snapshot, using
the transaction time for the lower bound. No schema or index change is required.
The existing statement timeout, job deadline, resource ceilings, daily singleton,
row/artifact/storage limits, purge, embargo and both-outcome gates remain intact.
The batch size is an I/O default, not an admission or model-quality threshold.
Selection phase and counts are recorded in the existing job outcome, without
credentials, SQL contents or observation payloads.

## Verification and remaining gate

The local read-only proof selected the full row limit without cancellation.
This is a G5-to-production database selection proof, not a production training
run or provider latency benchmark. PostgreSQL SELECT-only fixtures compared
full old/new projected rows across batch sizes and row limits, including owner
isolation, tied timestamps, wrong contracts/horizons and unknown/incomplete labels.
Unit tests cover batch exhaustion, bounds, cancellation and snapshot cleanup.

The current latest-row cohort still fails temporal support. Original feature
eligibility and purge/embargo were evaluated locally on the selected snapshot;
validation, calibration and test became empty. No candidate was trained,
registered, approved or promoted. Fixing query execution does not establish
model readiness. Selecting a longer temporal sample rather than the newest rows
would change dataset policy and requires a separately reviewed decision;
reducing the embargo or outcome gates is not part of this fix.

## Publication boundary

The patch is prepared locally only. Merge, production upload and production
training have not been authorized for this correction. After specific approval:
publish a draft PR, pass CI/review, merge normally, verify clean canonical source,
and upload that exact source to `scalpyn-pump-ml` only. This service uses uploaded
source, so merging alone does not update its scheduled job. A controlled run must
respect the existing daily singleton; do not delete its ledger to force a retry.
The run may correctly remain blocked by temporal gates. Keep inference, ML delta,
pool connection, MLShadow, profiles, exits and provider resources unchanged.
Read-only snapshots can hold older row versions for the selection duration;
monitor bounded runtime and DB load before adopting this release. Rollback is a
normal revert and canonical re-upload of the isolated job, not a profile change.
