# L3_PROFILE ML integrity remediation — 2026-09-16

Scope: L3_PROFILE / source L3. Preserve economic parameters, historical observations, temporal frontier and activation policy. No migration or configuration mutation is required.

## Implemented

- Select the active economic contract instead of hardcoded ATR v2. Require persisted net returns for the positive-net-return label. Resolve measurement state at the frozen query cutoff.
- L3 readiness calls the trainer loader and chronological split, reports exact exclusions and separate candidate/independent-holdout readiness, and displays the CatBoost automatic-training flag without enabling it.
- Project native L3 captures to ML inputs and their raw dependencies. Keep observational context on the original decision. Optional score inputs lacking provenance become missing; required inputs and future timestamps remain invalid. Preserve live source timestamps, including unchanged numerical values.
- Serve selected features in artifact name order, preserve missing values, validate required fields and categorical encodings. Other lanes retain their existing projection.
- Group by market event across profiles, weight repeated observations, require independent holdout events and days before fitting, and persist reproducible day-cluster AUC uncertainty.
- Register immutable full contract definitions in existing registry tables in the same transaction as future models. Artifact metadata includes implementation digest and dependency versions. Legacy model history remains intact.
- Default the model screen to L3_PROFILE, order by creation time, distinguish dates, show stored gate reasons and execution authority, correct validation sample display and the positive-return label description.

## Verification before release

- [command] Focused backend suite: 182 passed. Frontend dataset-audit tests: 2 passed. Next.js production build and TypeScript: PASS.
- [production query + read-only replay] At 2026-09-16T12:14:07.499789Z, 11 native trainer candidates all use v2. Active contract is shadow_atr_dynamic_v3; compatible population is 0. Configured candidate floor remains 1200.
- [saved production artifact replay] v98 model 27dd19c2-39e9-491c-9f85-73d86ac7007d: all 214 holdout vectors produce identical probabilities with corrected serving; max absolute difference 0.0, tolerance 1e-7, changed threshold decisions 0. No model activation or orders.
- [production query + replay] 10 active L3 watchlists: 0 canonical/effective entry-trigger mismatches. 58 threshold boundary cases: 29 rejected violating cases; 30 recent persisted shadows have nonempty rules snapshots. SKIPPED statuses in the sampled 30 recent decisions: 0. This sample does not prove all historical decisions valid.
- [command] graphify update completed (generated graph is ignored).

Detailed evidence is retained in the operator workspace: auditoria-l3-profile-20260916/correcoes. Deployment and authenticated UI evidence must be added to the release ledger after publication, not inferred from these checks.

## Remaining acceptance gates

Production must demonstrate new causal captures, valid profile authorization, READY/OK measurements and matured rows admitted by the exact loader. Existing SOURCE_REQUIRED / period / profile-authorization errors must not be relabeled valid; reconstructing or changing profile source definitions requires canonical evidence. No fabricated backfill is included.

Training remains blocked while the exact population or independent holdout is insufficient. No candidate fit, promotion, automatic flag change or financial activation is part of this release. The published fixes do not imply completion of future data accumulation, a successful production candidate or a profitable model.

Rollback uses a reviewed canonical revert commit and preserves persisted history. Newly registered contracts are immutable and additive; no schema rollback is needed.
