# Pump continuity v2 — observation rollout

Scope approved 2026-10-01: implement, merge through normal repository policy,
deploy and verify. The approval does not authorize capital orders, new Pool
admission, changes to legacy exits or ML Shadow, or unvalidated ML scoring.

## Contract and inventory

The source baseline is main b41c8b059145f45d20a9a214f03287938b198b62.
The legacy Pump cycle remains 30s; the additive adapter persists the first
capture for each UTC minute, instrument and owner. It keeps the actual capture
time rather than pretending a late cycle happened at the minute boundary.
Gate best ask anchors the outlook. A closed bar is never a current quote.
Quote/source timestamps, availability at retrieval, original producer
availability when present and stale/missing reasons accompany the observation.

Owner-scoped read routes under `/api/pump-monitor/opportunities`: current,
history with stable UUID cursor, observation detail, intelligence, AND explore
and a separate config API. Current reads do not create a decision. The detail
does not change its selected reference when the live snapshot refreshes.
The classic monitor remains accessible from `/pump-monitor`.

Migration `231_pump_opportunity_v2` creates only Pump-owned observations,
labels, experiments and predictions. No existing table or critical startup
column is changed. Downgrade intentionally preserves audit records. Label v1
and its jobs remain unchanged. v1 exports now preserve their dictionary hash
and ordered dictionaries; v2 export streams complete JSONL payloads and labels.

The instrument identity includes venue, spot pair and listing ID. If no
operator-verified listing ID is configured, `listing_unverified` is explicit;
those rows are never admitted to ML training. No automatic claim of detecting
exchange relistings is made. Configuring a verified new listing starts a new
instrument and episode instead of joining histories.

## Provisional defaults — simulation only

Authoritative defaults are `pump_opportunity_engine.DEFAULT_CONFIG`, separately
stored as config type `pump_opportunity`, audited through ConfigService and
editable in the observation UI. They are not calibrated recommendations:

```json
{"score_unit":"confirmation_points","simulation_threshold":20,
 "groups":{"flow":{"points":10},"structure":{"points":10},"acceptance":{"points":10}},
 "max_quote_age_seconds":60,"max_feature_age_seconds":300,"freshness_seconds":120,
 "episode_gap_seconds":180,"visual_exit_cycles":2,
 "risks":{"max_spread_pct":null,"max_slippage_pct":null,"max_extension_atr":null}}
```

Flow, CVD, taker, volume and persistence cannot be placed in multiple scoring
groups. Their contribution is one capped group. Group independence remains a
research hypothesis. Null inputs never renormalize remaining groups. Missing
quote, incomplete confirmation data, failed collection, unavailable liquidity
or observed exhaustion veto simulated eligibility regardless of points. Null
risk limits mean unconfigured, not evidence of safety. The old normalized
0–100 score and its current Pool limit are not changed or converted.

The new score is ALWAYS disconnected from Pool and Shadow. ML delta is always
zero. API validation rejects enabling Pool connection, training, inference or
ML delta; each requires another validated release and specific authorization.

## Labels and maturity

Targets +0.60% and +0.80% are gross. Horizons are 5/10/15/30/60/120 minutes.
V2 labels use each observation's frozen specification and mature separately.
Only full closed one-minute intervals within the decision/end window count.
Boundary high/low cannot prove an event after a decision made mid-minute.
An observed touch is true despite a gap, but first touch is censored when an
earlier gap or boundary exists. Incomplete absence remains unknown. Endpoint
requires an exact admissible endpoint; complete MFE/MAE require full coverage.
Same-bar target/downside ordering is ambiguous; stop-first is only a separate
conservative convention. Cost policy defaults null; no fabricated net outcome.

With current minute sources, mid-minute decisions generally have ambiguous
boundary/endpoint outcomes. This is deliberate uncertainty, not model-ready
negative samples. Trade-resolution endpoints require a further captured data
source/contract before their accuracy can be claimed. Descriptive patterns
report denominators, episodes, instruments, days and unknowns, never validated
probability or cluster confidence intervals. Recent all-candidate samples are
bounded and their selection policy is explicit.

Excluded instruments with pending v2 horizons receive only bounded raw price
support. Support does not build a score, observation or new label target and
does not enter admission. The USD 1B filter remains the legacy universe source;
market-cap-specific freshness remains uncertified.

## Resource inventory and activation budget

Provider baseline [query, `railway-baseline.json`] gives existing Pump worker
limits CPU 1 and RAM 2,000,000,000 bytes. API and research each have CPU 2 and
RAM 5,000,000,000 bytes; beat CPU 1/RAM 1,000,000,000 bytes. Database baseline
[query, `baseline.jsonl`] is 53,654,193,855 bytes, 54 connections (2 active).
These are observed capacity settings, not measured spare headroom.

Light adapter budgets [config, provisional]: at most 100 sorted candidates per
minute, write timeout 4s, SQL statement timeout 3s, one transaction from the
existing dedicated Pump worker. Label budget: 100 observation/horizon pairs,
timeout 4s, statement timeout 3s, no parallel labels or automatic retries.
Labels may be enabled only after bounded runtime measurement of the adapter.
The UI/API reads never trigger training. Retention configuration is 30 days;
automatic destructive purge of v2 raw history is intentionally not scheduled
until the retention policy is reviewed. DB growth must be monitored.

No new provider service, credential, billable trainer or ML schedule is created.
Offline Pump XGBoost challenger code requires an explicit resource/support/split
manifest before loading heavy libraries, purges 120-minute crossing labels and
episodes, separates train/validation/calibration/test, records precision,
recall/rejected winners and calibration metrics, and writes only `pump_ml/`.
The Pump registry has no production model pointer or auto-promotion path.
This infrastructure is not evidence of a trained or validated model. Dedicated
ML worker/queue activation, CPU/RAM/I/O budget and failure/load comparison
against Shadow remain blocked until capacity and budgets are approved.

## Rollout, checks and rollback

1. Focused contract tests and existing Pump regressions; frontend build/lint.
2. Alembic single head/history and read-only prod critical-schema audit.
3. Draft PR, CI, normal merge with no protection bypass.
4. Production source guard on clean HEAD equal to origin/main; track exact
   backend and frontend artifacts to terminal status.
5. Verify additive schema, bounded logs and API health. Activate only `enabled`
   and `ui_enabled` for the approved owner using ConfigService; read back from
   a fresh process. Compare legacy config/Pool overrides and Shadow model
   artifacts/counters to baseline. Then evaluate bounded label activation.
6. Authenticated UI proof must cover radar, selected fixed reference,
   intelligence/unknowns and config flags. A 401 is routing proof only.
7. Record release ledger only after full runtime and authenticated UI PASS.

Rollback config: set `enabled=false`, `ui_enabled=false`, `labels_enabled=false`;
leave ML/delta/connection false. Revert source/UI through normal main release
when necessary, preserving tables and raw history. No deletion or relabeling.
Test the disabled path and keep the classic monitor available throughout.

## Acceptance limits

The new unit suite maps T01–T29 where deterministic fixtures can establish
semantics. It does not establish provider-level load isolation, calibrated
model quality, real relisting detection or authenticated browser proof.
Runtime observations and resource failure trials need their own evidence.
Heavy jobs, statistical promotion and future connection are intentionally
inactive until the plan's independent acceptance conditions are met.
