# Robust Indicators — Phase 1 (Shadow Mode)

> **Status — superseded by Phase 3 (deprecation).** The robust engine
> is now the formal default for every symbol; the legacy pipeline is
> on standby behind the single ``LEGACY_PIPELINE_ROLLBACK`` flag. This
> document is preserved as the historical record of the shadow-mode
> design and the per-symbol bucket math (still used as a diagnostic).
> See ``backend/docs/phase3_deprecation.md`` for the current contract,
> rollback runbook, and 7-day observation checklist.

## Why this exists

The legacy `feature_engine` + `score_engine` produces a single flat dict of
indicator values and a single composite alpha score. That gives us no way to
reason about source quality or freshness; for example, a candle-derived
`taker_ratio` masquerades as a real flow signal.

Phase 1 lays the groundwork for fixing that without changing user-visible
behaviour:

1. Wrap every indicator value into an `IndicatorEnvelope` carrying source +
   timestamp + confidence.
2. Run integrity validation (5 rules) and a confidence-weighted score
   alongside the legacy path.
3. Persist the result to `indicator_snapshots` and emit Prometheus metrics so
   we can compare the two pipelines.

The new path **only reads, computes, persists snapshots and logs
divergence**. Legacy scoring stays authoritative; gating is via
`USE_ROBUST_INDICATORS=true|false` (default `false`).

## Architecture

```
backend/app/services/robust_indicators/
├── __init__.py           # public re-exports
├── envelope.py           # IndicatorEnvelope, IndicatorStatus, DataSource,
│                         # CONFIDENCE_MAP, STALENESS_PENALTY, wrap_indicator
├── validation.py         # 5 rules → ValidationResult
├── score.py              # critical-gate, confidence-gate, weighted score
├── compute.py            # envelope_indicators() adapter + on-demand fetcher
├── metrics.py            # 5 Prometheus metrics + divergence_bucket()
├── snapshot.py           # persist_snapshot() + ensure_snapshot_table()
└── shadow.py             # run_shadow_scan() — entry point
```

### Confidence model

Each `IndicatorEnvelope` carries:

* `source` → `DataSource` enum (gate trades / candles / orderbook, binance,
  merged, candle fallback, derived, unknown).
* `base_confidence` → looked up from `CONFIDENCE_MAP`.
* `staleness_seconds` → derived from envelope `timestamp`.
* `confidence` → `base_confidence` multiplied by the piecewise
  `STALENESS_PENALTY` table (1.0 < 60s, 0.85 < 180s, 0.5 < 300s, 0.1 above).
* `status` → `VALID` / `DEGRADED` / `NO_DATA` / `INVALID`.

### Validation rules (`validation.py`)

| Rule                               | Severity | Behaviour                                                                     |
|------------------------------------|----------|-------------------------------------------------------------------------------|
| `volume_delta_bucket_exclusivity`  | CRITICAL | `volume_delta` must originate from a flow source, not a candle approximation. |
| `critical_no_data`                 | CRITICAL | RSI / ADX / MACD must be `VALID` or `DEGRADED`.                               |
| `flow_primary_source`              | CRITICAL | Flow indicators must come from gate/binance trades or merged feed.            |
| `derived_dependencies`             | WARNING  | Derived indicators (e.g. `macd_histogram`) need their inputs to be usable.    |
| `sufficient_candles`               | WARNING  | Long warm-up indicators (`ema200`, `adx`, `rsi`) shouldn't be `NO_DATA`.      |

CRITICAL violations populate `errors` and force `passed=False`. WARNING
violations populate `warnings` only.

### Score engine (`score.py`)

```
critical_gate   → reject if any of (rsi, adx, macd) is NO_DATA
confidence_gate → reject if avg(envelope.confidence) < 0.6
weighted_score  → sum(rule.points * envelope.confidence) per category,
                  bounded to [0, 100]; aggregated by category weights
score_confidence = avg confidence of envelopes that drove matched rules
can_trade        = (score >= threshold) AND (score_confidence >= 0.6)
```

Rule shape is identical to the legacy `ScoreEngine` (`scoring_rules`/`rules`
list with `indicator`/`operator`/`value`/`points`/`category`) so the existing
config from `config_service` drives both pipelines unchanged.

### Persistence (`indicator_snapshots`)

Created by alembic revision `027_indicator_snapshots`. Columns:

```
id UUID PK
symbol VARCHAR(40)
timestamp TIMESTAMPTZ
indicators_json JSONB        -- {name → envelope.to_dict()}
global_confidence NUMERIC
valid_indicators / total_indicators INTEGER
validation_passed BOOLEAN
validation_errors JSONB      -- {errors: [...], warnings: [...]}
score / score_confidence NUMERIC
can_trade BOOLEAN
legacy_score NUMERIC          -- for direct A/B comparison
divergence_bucket VARCHAR(16) -- <1%, 1-5%, 5-10%, >10%, unknown
rejection_reason VARCHAR(255)
user_id / watchlist_id UUID
```

A composite index on `(symbol, timestamp DESC)` plus a best-effort
TimescaleDB `create_hypertable` call (no-op when the extension isn't
available).

### Metrics (`/metrics`)

Exposed via the new `backend/app/api/metrics.py` router. Metrics:

| Name                                          | Type      | Labels                          |
|-----------------------------------------------|-----------|---------------------------------|
| `indicator_computation_duration_seconds`      | Histogram | `symbol`, `indicator`, `source` |
| `indicator_confidence`                        | Gauge     | `symbol`                        |
| `indicator_staleness_seconds`                 | Gauge     | `symbol`, `indicator`           |
| `score_rejection_total`                       | Counter   | `reason`                        |
| `robust_vs_legacy_divergence_total`           | Counter   | `bucket`                        |

`prometheus-client` is an optional dependency — when it isn't installed the
metrics functions degrade to no-ops and `/metrics` serves a stub message.

### Slack alerts (`backend/app/tasks/robust_alerts.py`)

Celery beat task `app.tasks.robust_alerts.evaluate` runs every 90 seconds and
inspects rows in `indicator_snapshots` from the last 5 minutes. Conditions:

* `staleness` — max envelope age > 300s.
* `low_confidence` — avg `global_confidence` < 0.6 (≥ 5 samples).
* `rejection_rate` — > 50% snapshots rejected (≥ 5 samples).
* `divergence` — `>10%` bucket share above `ROBUST_ALERT_DIVERGENCE_PCT`
  (default 20%; ≥ 5 samples).

Each condition is rate-limited to one Slack notification every 15 minutes via
Redis (with an in-process fallback). Slack delivery reuses
`notification_service._send_slack`.

## Wiring into the legacy pipeline

`backend/app/tasks/pipeline_scan.py` calls `run_shadow_scan(...)` once per
watchlist scan, after the legacy assets/scores have been computed. The whole
block is wrapped in `try/except` so a shadow-side failure can never cascade
into the legacy path. Shadow mode is gated by
`is_shadow_enabled()` → reads `settings.USE_ROBUST_INDICATORS`.

## How to enable

```
export USE_ROBUST_INDICATORS=true
# restart backend + celery beat + celery worker
```

That's the only switch. To revert: unset the env var (or set it to `false`)
and restart the workers — the table and Prometheus metrics simply stop
filling.

## Phase 2 / future work

* Replace the `envelope_indicators()` adapter with on-demand `compute_indicators_robust()`
  so the robust pipeline becomes self-sufficient.
* Promote `score` / `can_trade` from snapshots to the authoritative path
  behind the same flag.
* Surface staleness + confidence in the front-end indicator drawer.
