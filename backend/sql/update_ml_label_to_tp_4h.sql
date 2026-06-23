-- Atualiza ml_win_fast_threshold_seconds de 1800 para 14400 (is_tp_4h_v1).
--
-- ATENÇÃO: este script altera o threshold de label para treino de novos modelos.
-- Modelos existentes (v29, v30, v35 etc.) NÃO são afetados — apenas o próximo
-- retreino via MLChallengerService / profile_intelligence_job usará is_tp_4h_v1.
--
-- Pré-requisitos:
--   1. Migration 104_ml_metrics_json aplicada (alembic upgrade head)
--   2. UI de métricas corrigida (PR deployado)
--   3. Testes de label passando (41/41)
--
-- Verificar antes:
--   SELECT config_json->>'ml_win_fast_threshold_seconds' FROM config_profiles
--   WHERE config_type = 'ml';
--
-- Executar:
--   psql $DATABASE_PUBLIC_URL -f update_ml_label_to_tp_4h.sql
--
-- Verificar após:
--   SELECT config_json->>'ml_win_fast_threshold_seconds',
--          config_json->>'enable_lightgbm',
--          config_json->>'enable_catboost'
--   FROM config_profiles WHERE config_type = 'ml';

UPDATE config_profiles
SET
    config_json = config_json || '{"ml_win_fast_threshold_seconds": 14400}'::jsonb,
    updated_at  = NOW()
WHERE config_type = 'ml';
