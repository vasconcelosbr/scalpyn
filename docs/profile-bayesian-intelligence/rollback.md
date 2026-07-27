# Profile Bayesian Intelligence — Rollback

## Immediate shutdown

1. Set every Profile Bayesian flag to false.
2. Stop workers consuming `profile_bayesian` and `profile_optimization`.
3. Confirm the API status endpoint reports all flags off.
4. Leave historical rows intact for audit.

This stops new work without mutating active profiles, candidates from other
origins, ML models, or trading state.

## Application rollback

The API router and frontend tab are additive. Rolling back the application
removes the visible surface while the database remains readable by audit tools.
The existing Profile Intelligence, Calibration Evolution, ML, and trading routes
do not depend on this package.

## Schema downgrade

Migration `137_profile_bayesian` has a reverse-order downgrade that drops only
Profile Bayesian tables and indexes. Run it only after:

- all dedicated workers are stopped;
- flags are false;
- audit-retention requirements permit deletion;
- no Bayesian draft still needs its lineage.

Downgrade does not touch `profiles`, `profile_versions`,
`profile_intelligence_autopilot_candidates`, ML tables, trading tables, or
source snapshots.

## Candidate safety

Existing shadow candidates created through the shared workflow retain source
evidence and remain governed by that workflow. Disabling this module does not
activate, reject, roll back, or delete them.
