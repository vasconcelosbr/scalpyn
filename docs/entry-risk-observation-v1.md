# Entry Risk Observation v1

## Status

- `LEGACY_SCORE_STATUS=OBSERVATIONAL_LEGACY`
- `MOMENTUM_INTENSITY_STATUS=MONITOR_ONLY`
- `EXHAUSTION_RISK_STATUS=MONITOR_ONLY`
- `OPERATIONAL_EFFECT=FALSE`
- `PRODUCTION_READY=FALSE`

The statistics quoted in the correction specification are not production
evidence from this implementation.  Reproduce them against a frozen export or
read-only database snapshot before citing them as current.

## Data lineage

`ohlcv(5m closed candles)` → `entry_risk_features` pure calculator →
`entry_risk_capture` reconciler → `shadow_trades.entry_risk_features_json` →
Shadow Portfolio detail/export → analytical dataset/Intelligence Runs.

The existing flat `features_snapshot`, score engine, gates, consolidator and
execution paths remain unchanged.  Indicator metadata now records its source
timeframe, but latest-timestamp selection is unchanged.

## Schema and configuration

Migration `196_entry_risk_observation` adds the JSON contract, capture status
and capture timestamp.  Existing rows start as `NOT_AVAILABLE`.  New rows start
as `PENDING`; a 5-minute idempotent task resolves them to `VALID`, `PARTIAL`,
`INVALID` or `ERROR`. Historical rows without demonstrable lineage are
`INVALID` with reason `LEGACY_UNVERIFIABLE`. Historical rows can be classified with:

```powershell
python backend/scripts/backfill_entry_risk_features.py
python backend/scripts/backfill_entry_risk_features.py --apply --limit 100
```

`config_profiles.entry_risk_observation` is seeded with both operational flags
false and a 300-second source freshness limit.  The API schema rejects `true`, and runtime profile validation rejects
the two candidate score names in executable conditions.  Promotion therefore
requires a future, explicit contract/code change rather than a hidden flag.

## Baseline and non-regression evidence

Before deployment, freeze the exact input dataset and record:

- query text and bound parameters;
- UTC interval and allowed sources/outcomes;
- row count, distinct trade IDs and SHA-256 of canonical JSON rows;
- current L1/L2/L3 decisions, selected profile, technical/final scores, trade
  count, barriers and consolidation result.

Replay the same inputs after the change and compare canonical JSON after
removing only `entry_risk_*` fields and capture timestamps.  Acceptance is zero
differences.  The unit suite also compares the extracted legacy calculator with
the previous formula for multiple ATR periods.

## Observability and reconciliation

Prometheus publishes computation status, data-quality reasons, latency, null
rate and distribution-drift metrics with bounded labels.  Symbol, profile and
trade IDs are kept in structured logs.  The authenticated endpoint
`GET /api/shadow-trades/entry-risk/reconciliation` exposes capture counts; the
offline report additionally checks missing hashes and legacy snapshot mismatch.

## Shadow validation

Collect four complete weeks without operational effects.  Freeze discovery,
two consecutive temporal validation windows and a final holdout.  Report AUC,
SL capture, TP collateral, net counterfactual P&L, coverage, reconstruction,
null rate and stability by profile family, symbol and regime.  Candidate scores
remain null until a versioned formula generalizes out of time.  The first
permitted experiment is `SCORE_PENALTY` or `REQUIRE_CONFIRMATION` in shadow;
`HARD_BLOCK` is not permitted.
