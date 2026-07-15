# ML Challenger Readiness Plan

LightGBM and CatBoost remain non-implemented and non-operational. This plan
defines the prerequisites for introducing them as challengers without changing
the active XGBoost production path.

## LightGBM challenger

Initial feature scope: RSI, ADX, MACD, ATR, EMA distance, spread, depth, volume,
taker ratio, volume delta, VWAP distance, and Bollinger Band width.

Required implementation:

- explicit dependency and reproducible lockfile;
- trainer using the same temporal discovery/validation/test contract;
- predictor isolated from the production champion;
- artifact registration in `ml_model_registry`;
- model-specific validation metrics and calibrated threshold;
- benchmark against the incumbent XGBoost on the same dataset version;
- shadow-forward predictions with `model_id`, `source_run_id`, and audit log;
- promotion path requiring human approval and rollback payload.

## CatBoost challenger

Initial categorical scope: profile name, market regime, strategy skill, symbol
cluster, session, entry pattern, and Auto-Pilot context.

Required implementation:

- documented categorical encoding and missing-value policy;
- trainer and predictor with feature-schema version enforcement;
- the same registry, benchmark, shadow-forward, approval, and rollback gates
  required for LightGBM.

## Production restrictions

- LightGBM and CatBoost must remain `candidate` or `challenger`.
- A challenger cannot change the active threshold, Auto-Pilot configuration, or
  production decisions.
- Only one `champion` may exist per profile, market regime, and strategy skill.
- UI status remains “Não implementado” until trainer, predictor, dependencies,
  tests, audit, benchmark, and registry integration are complete.
