# Pump approved-plan acceptance matrix

This is a status matrix, not a claim that every named unit test completes its
corresponding operational acceptance. The approved Library plan is
`libfile_8ce5c207284c81919078fb57702d9f89` (476 lines). Runtime measurements,
provider IDs and cutoffs live in the deployment report and snapshot artifacts.

| Plan scope | Implemented / evidenced | Pending acceptance or limitation |
|---|---|---|
| P0 inventory / contracts / budget | Routes, consumers, owner IDs, flags, immutable current ask, logical cadence, confirmation units, service limits and baseline hashes recorded | Shadow-specific latency under Pump load and operational alert owner/recipient/tolerance approval are not a completed benchmark |
| P1 temporal data / identities | Frozen availability, reference/spec/cost/producer hashes, owner and listing evidence, unknown reasons, independent episodes, immutable slots, raw revisions; export and idempotent TEMP transaction/lock tests | Unverified provider epochs remain explicitly unverified; no historical enrichment |
| P2 radar / detail / intelligence | Three views, AND ledger and flow cap, risk/missingness veto, separate visual state, disconnected simulation, pagination/history/owner-scoped reads; ML contribution zero | Final authenticated feature QA; configured risk limits and calibrated weights remain explicit product/research choices, not silently activated |
| P3 targets / windows / research | Separate gross targets, endpoint and time-to-touch, six horizons including downside at 10 minutes, exact trades, gap/censor/order semantics, bounded FIFO queue, all-candidate descriptive catalog and support | V4 pre-touch metric prepared and tested; publication, activation and runtime proof pending. Clustered uncertainty/confirmation cohorts and economically known net costs are not validated |
| P4 isolated ML | Dedicated dataset/registry/artifact/job, resource ceilings, temporal manifest/purge/embargo, native challenger/calibrator code, neutral status; first daily run blocked cleanly for support | A mature challenger, statistical minimum/effect decision, final untouched evaluation, observational inference and incremental-benefit/stratum validation remain pending; no validated probability is claimed |
| P5 operation / release | Protected merges, source guard, terminal providers for current release, TEMP load/failure/idempotency tests, unchanged execution/Shadow configuration, current-price observation live | Sustained queue acceptance, authenticated UI proof, full release ledger and real rollback exercise not complete. No score/ML execution activation authorized |

## Mandatory test evidence by acceptance family

| Plan tests | Available evidence | Additional acceptance needed |
|---|---|---|
| T01–T11, T13–T19, T29 | Deterministic temporal/boundary/target/unknown/hash/listing/ledger/veto/unit/order tests; exact trade regressions | Production data support does not certify calibrated prediction quality |
| T12 | Universe/support drain regression, cap eligibility and excluded-symbol separation | Preserve existing support paths in each release |
| T20, T24 | Stable slot/owner identity tests plus real PG TEMP duplicate, owner read isolation, atomic commit/retry and lock exclusion | Authenticated cross-owner UI/API QA has not been run on final deployment |
| T21–T23 | Purged/embargo split and minimum-support guards; selection metrics expose rejected winners/recall | Cluster-aware confidence intervals, sufficient independent cohorts and actual challenger comparison not complete |
| T25 | Fixed ceilings, dense bounded PG prefix and explicit oversized-window block; resource/runtime snapshots | Sustained production lag and Shadow latency/throughput comparison, including failure isolation, remain acceptance items |
| T26 | Invalid activation flags rejected; pointer/flag rollback procedure preserves history | Actual restoration exercise is not equivalent to the named flag unit test and remains pending |
| T27 | Immutable selected/episode reference tests and detail rendering | Authenticated visual verification pending |
| T28 | Scoped source diff/tests and protected configuration hashes | Final exact-deployment runtime/UI evidence remains required |

No training maturity wait is imposed on radar use. These pending research
acceptances must not hide missing operational features or be described as
completed merely because model contribution remains zero.
