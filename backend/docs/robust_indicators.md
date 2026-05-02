# Robust Indicators — Architecture (Post-Phase-4)

This document describes the steady-state architecture after Phase 4 of the
Robust Indicators rollout. Earlier phase-by-phase rollout notes have been
removed; the rollout itself (shadow → dual-write → robust-authoritative →
cleanup) is complete and the corresponding feature flags no longer exist.

## Pipeline overview

```
       MarketDataService / OHLCV / order flow
                       │
                       ▼
            FeatureEngine.compute_features
                       │   raw indicator dict
                       ▼
       envelope_indicators(symbol, indicators)
                       │   Dict[str, IndicatorEnvelope]
                       ▼
        validate_indicator_integrity(envelopes)
                       │   ValidationResult (gates)
                       ▼
     calculate_score_with_confidence(envelopes, rules)
                       │   ScoreResult { score, confidence, rejected, ... }
                       ▼
   pipeline_scan / evaluate_signals / execute_buy
```

There is exactly one scoring path. `ScoreEngine.compute_score()` is kept as
the *legacy-compatible* convenience wrapper used by `compute_scores`
(persists into `alpha_scores` for back-compat consumers) and a handful of
read APIs that need a per-rule breakdown for the UI. Every execution
decision (which symbols enter L3, which trigger entries, which trades fire)
runs through the robust engine.

## Confidence model

Each indicator value is wrapped in an `IndicatorEnvelope` carrying:

* `value` — the numeric reading (or `None` for `NO_DATA`)
* `source` — the data source enum (`GATE_TICKER`, `GATE_TRADES`,
  `GATE_CANDLES`, `BINANCE_FALLBACK`, `CANDLE_FALLBACK`, `INTERNAL_DERIVED`)
* `confidence` — `base_confidence × staleness_multiplier`, clamped to
  `[0, 1]`. The base confidence per source lives in `CONFIDENCE_MAP`;
  staleness penalties live in `STALENESS_PENALTY`.
* `status` — `VALID`, `DEGRADED`, `STALE`, or `NO_DATA`.

Volume-flow indicators (`volume_delta`, `taker_ratio`) are now strict —
when the primary order-flow source has not provided a real value the
envelope is `NO_DATA` and the engine never substitutes a candle-derived
approximation. (Phase 4 removed `allow_candle_fallback` from the
`config_profiles` JSON; migration `029_strip_candle_fallback` performs the
one-shot cleanup of any user configs that still carried the key.)

## Validation gates

`validate_indicator_integrity` enforces the integrity contract before any
score is computed:

* `critical_no_data` — every entry in the critical set must be `VALID` or
  `DEGRADED`. The set includes RSI, ADX, MACD (all three components),
  EMA9/50/200, VWAP, taker_ratio, buy_pressure, volume_delta.
* `derived_dependencies` — derived indicators must have all their inputs
  available (e.g. `macd_histogram` requires `macd` and `macd_signal_line`).
* `volume_delta_bucket_exclusivity` — `volume_delta` must not come from
  candles AND must equal `taker_buy − taker_sell` within tolerance.
* `sufficient_candles` — long-warmup indicators (EMA200) must not be
  `NO_DATA`.
* `confidence_floor` — global confidence (the volume-weighted average of
  envelope confidences) must clear a configurable threshold.

A failure on any rule with severity `CRITICAL` causes the engine to emit
a rejected `ScoreResult` (`rejected=True`, `rejection_reason="critical_gate:…"`)
and the pipeline drops the symbol from execution.

## Scoring formula

`calculate_score_with_confidence` evaluates the configured rule set against
the envelope dict. For each rule the contribution is
`points × envelope.confidence` for the indicator the rule references; the
final score is

```
score = (Σ contributions / Σ |points|) × 100
```

clamped to `[0, 100]`. Category weights are accepted for back-compat but no
longer alter the result — the confidence weighting is applied directly to
each rule's points so the score is independent of category shuffling.

## Persistence

* `alpha_scores` — populated by `compute_scores` for back-compat. Always
  `scoring_version='v1'`; `alpha_score_v2` and `confidence_metrics` are
  retained as nullable columns but are not written.
* `indicator_snapshots` — populated by `persist_snapshot` and contains
  the envelope payload, ValidationResult, and ScoreResult for the most
  recent compute cycle. The legacy `divergence_bucket` column is
  retained as nullable for forward compatibility.
* `pipeline_watchlist_assets` / `pipeline_watchlist_rejections` — both
  carry an `engine_tag` column (`'robust'` since Phase 4) so audit
  queries can confirm which engine produced any historical row.

## Operational alerts

`backend/app/tasks/robust_alerts.py` runs every ~90s and emits ops-only
Slack notifications when one of the following sustained-window conditions
trips (rate-limited to one alert per 15 minutes):

* `staleness` — max envelope age > 300s sustained for 2 minutes.
* `low_confidence` — average confidence < 0.6 sustained over 5 minutes.
* `rejection_rate` — rejection ratio > 50 % sustained over 5 minutes.

Alerts are delivered to the single `ROBUST_ALERTS_OPS_WEBHOOK_URL`
endpoint — there is no per-tenant Slack broadcast.

## Removed surface (Phase 4)

For grep-ability, the following symbols were removed during the cleanup:

* `ScoreEngine.compute_alpha_score` (renamed to `compute_score`)
* `robust_indicators.select_score`, `bucketing`, `shadow`, `preflight`
* `robust_indicators.metrics.divergence_bucket` /
  `increment_divergence`
* `robust_indicators.is_shadow_enabled` / `is_legacy_rollback_active` /
  `run_shadow_scan` / `select_authoritative_score`
* Settings: `USE_ROBUST_INDICATORS`, `USE_ROBUST_INDICATORS_PERCENT`,
  `LEGACY_PIPELINE_ROLLBACK`, `FORCE_ROLLOUT_RAISE`,
  `ROBUST_FORCE_SYMBOLS`, `ROBUST_EXCLUDE_SYMBOLS`
* Config keys: `allow_candle_fallback*`, `dual_write_mode`,
  `confidence_weighting`
* Endpoints: `/api/admin/robust-indicators/status`
* Beat task: `app.tasks.robust_alerts.check_legacy_rollback_standby`
