-- Scalpyn ML + Auto-Pilot + Profile Intelligence audit
-- Read-only queries. Execute inside:
-- BEGIN TRANSACTION READ ONLY;
-- ...
-- ROLLBACK;

SELECT user_id, config_json
FROM config_profiles
WHERE config_type IN ('profile_intelligence', 'autopilot_guardrails', 'ml')
  AND is_active = true
ORDER BY config_type, updated_at DESC;

SELECT status, COUNT(*) AS count
FROM profile_intelligence_runs
WHERE run_at >= NOW() - INTERVAL '30 days'
GROUP BY status ORDER BY status;

SELECT id, user_id, run_at, status, engine_version, lookback_days,
       total_profiles, total_shadow_trades, total_closed_trades,
       base_win_rate, error_message, settings_json
FROM profile_intelligence_runs
ORDER BY run_at DESC LIMIT 10;

SELECT event_type, COUNT(*) AS count
FROM profile_intelligence_audit_log
WHERE run_id = (SELECT id FROM profile_intelligence_runs ORDER BY run_at DESC LIMIT 1)
GROUP BY event_type ORDER BY event_type;

SELECT
  COUNT(*) AS total,
  COUNT(*) FILTER (WHERE run_id IS NULL) AS missing_run_id,
  COUNT(*) FILTER (WHERE source_combination_id IS NULL) AS missing_combination_id,
  COUNT(*) FILTER (WHERE source_profiles IS NULL OR source_profiles = '[]'::jsonb) AS missing_source_profiles,
  COUNT(*) FILTER (WHERE confidence_score IS NULL) AS missing_confidence,
  COUNT(*) FILTER (WHERE evidence_summary_json IS NULL) AS missing_evidence
FROM profile_suggestions;

SELECT status, COUNT(*) AS count
FROM profile_suggestions
GROUP BY status ORDER BY status;

SELECT
  COUNT(*) AS total,
  COUNT(*) FILTER (WHERE model_provider IS NOT NULL) AS with_model_provider,
  COUNT(*) FILTER (WHERE model_name IS NOT NULL) AS with_model_name,
  COUNT(*) FILTER (WHERE lower(COALESCE(model_name,'')) LIKE '%lightgbm%') AS lightgbm,
  COUNT(*) FILTER (WHERE lower(COALESCE(model_name,'')) LIKE '%catboost%') AS catboost
FROM profile_intelligence_audit_log;

SELECT combination_type, COUNT(*) AS count,
       COUNT(*) FILTER (
         WHERE validation_metrics_json IS NOT NULL
           AND validation_metrics_json <> '{}'::jsonb
       ) AS with_validation,
       COUNT(*) FILTER (
         WHERE source_profiles IS NOT NULL
           AND source_profiles <> '[]'::jsonb
       ) AS with_source_profiles,
       COUNT(*) FILTER (WHERE overfit_risk) AS overfit
FROM profile_rule_combinations
GROUP BY combination_type ORDER BY combination_type;

SELECT COUNT(*) AS total,
       COUNT(*) FILTER (
         WHERE source_profiles IS NOT NULL
           AND source_profiles <> '[]'::jsonb
       ) AS with_source_profiles
FROM profile_indicator_stats;

SELECT id, version, status, model_scope, profile_id, source_filter,
       roc_auc, precision_score, f1_score, false_positive_rate,
       decision_threshold, activated_at, retired_at,
       feature_schema_version, feature_count, dataset_hash, query_hash
FROM ml_models
ORDER BY created_at DESC;

SELECT model_scope, profile_id, COUNT(*) AS active_count
FROM ml_models
WHERE status = 'active'
GROUP BY model_scope, profile_id
HAVING COUNT(*) > 1;

SELECT
  COUNT(*) AS total,
  COUNT(*) FILTER (WHERE lower(COALESCE(notes,'')) LIKE '%lightgbm%') AS lightgbm,
  COUNT(*) FILTER (WHERE lower(COALESCE(notes,'')) LIKE '%catboost%') AS catboost,
  COUNT(*) FILTER (
    WHERE lower(COALESCE(notes,'')) LIKE '%xgboost%'
       OR model_blob IS NOT NULL
  ) AS xgboost_or_blob
FROM ml_models;

SELECT COUNT(*) AS total,
       COUNT(*) FILTER (WHERE model_id IS NULL) AS missing_model_id,
       COUNT(DISTINCT model_id) AS distinct_models
FROM ml_predictions;

SELECT COUNT(*) AS profiles,
       COUNT(*) FILTER (WHERE config ? 'scoring') AS with_scoring,
       COUNT(*) FILTER (WHERE config ? 'block_rules') AS with_block_rules,
       COUNT(*) FILTER (WHERE config ? 'entry_triggers') AS with_entry_triggers,
       COUNT(*) FILTER (WHERE auto_pilot_enabled) AS autopilot_enabled
FROM profiles;

SELECT action, COUNT(*) AS count
FROM autopilot_audit_logs
GROUP BY action ORDER BY action;

SELECT COUNT(*) AS total,
       COUNT(*) FILTER (WHERE config_before IS NULL) AS missing_before,
       COUNT(*) FILTER (WHERE config_after IS NULL) AS missing_after,
       COUNT(*) FILTER (WHERE version_id IS NULL) AS missing_version
FROM autopilot_audit_logs;

SELECT user_id, enabled, settings_json, last_cycle_at
FROM profile_intelligence_autopilot_settings;

SELECT state, COUNT(*) AS count
FROM profile_intelligence_autopilot_candidates
GROUP BY state ORDER BY state;

SELECT event_type, COUNT(*) AS count
FROM profile_intelligence_autopilot_audit
GROUP BY event_type ORDER BY event_type;

SELECT COUNT(*) AS total,
       COUNT(*) FILTER (WHERE profile_id IS NULL) AS missing_profile_id,
       COUNT(*) FILTER (WHERE source IS NULL) AS missing_source,
       COUNT(*) FILTER (WHERE outcome IS NULL) AS missing_outcome,
       COUNT(*) FILTER (
         WHERE features_snapshot IS NULL OR features_snapshot = '{}'::jsonb
       ) AS empty_features,
       COUNT(*) FILTER (WHERE pnl_pct IS NULL) AS missing_pnl
FROM shadow_trades;

SELECT source,
       COUNT(*) AS total,
       COUNT(DISTINCT profile_id) AS distinct_profiles,
       COUNT(*) FILTER (WHERE profile_id IS NULL) AS missing_profile_id
FROM shadow_trades
GROUP BY source ORDER BY total DESC;

SELECT COUNT(*) AS duplicate_groups
FROM (
  SELECT profile_id, symbol, source, date_trunc('hour', created_at), COUNT(*)
  FROM shadow_trades
  GROUP BY profile_id, symbol, source, date_trunc('hour', created_at)
  HAVING COUNT(*) > 1
) d;

SELECT
  COUNT(*) FILTER (WHERE outcome IN ('TP_HIT','SL_HIT','TIMEOUT')) AS closed,
  COUNT(*) FILTER (
    WHERE outcome IN ('TP_HIT','SL_HIT','TIMEOUT')
      AND holding_seconds IS NULL
  ) AS closed_missing_holding,
  COUNT(*) FILTER (WHERE outcome = 'TP_HIT' AND pnl_pct <= 0) AS tp_non_positive_pnl,
  COUNT(*) FILTER (WHERE outcome = 'SL_HIT' AND pnl_pct >= 0) AS sl_non_negative_pnl,
  COUNT(*) FILTER (WHERE outcome = 'TIMEOUT') AS timeouts
FROM shadow_trades;

SELECT
  COUNT(*) FILTER (WHERE features_snapshot ? 'score') AS with_score,
  COUNT(*) FILTER (WHERE features_snapshot ? 'direction') AS with_direction,
  COUNT(*) FILTER (WHERE features_snapshot ? 'signal_direction') AS with_signal_direction,
  COUNT(*) FILTER (WHERE features_snapshot ? 'outcome') AS with_outcome,
  COUNT(*) FILTER (WHERE features_snapshot ? 'pnl_pct') AS with_pnl,
  COUNT(*) FILTER (WHERE features_snapshot ? 'exit_price') AS with_exit_price,
  COUNT(*) FILTER (WHERE features_snapshot ? 'max_profit') AS with_max_profit
FROM shadow_trades
WHERE features_snapshot IS NOT NULL;

SELECT COUNT(*) AS total,
       COUNT(*) FILTER (WHERE features_snapshot ? 'rsi') AS rsi,
       COUNT(*) FILTER (WHERE features_snapshot ? 'adx') AS adx,
       COUNT(*) FILTER (WHERE features_snapshot ? 'taker_ratio') AS taker_ratio,
       COUNT(*) FILTER (WHERE features_snapshot ? 'volume_delta') AS volume_delta,
       COUNT(*) FILTER (WHERE features_snapshot ? 'atr_pct') AS atr_pct,
       COUNT(*) FILTER (WHERE features_snapshot ? 'vwap_distance_pct') AS vwap_distance_pct
FROM shadow_trades
WHERE features_snapshot IS NOT NULL;

