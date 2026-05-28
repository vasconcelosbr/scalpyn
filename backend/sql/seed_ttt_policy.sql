-- Seed: TTT (Time-To-Target) Policy
-- Registra a política TTT global no config_profiles para auditoria e
-- controle via dashboard. Os valores aqui são a fonte de verdade para
-- documentação — o runtime lê de variáveis de ambiente (Cloud Run).
--
-- Passos para aplicar via gcloud:
--   1. gsutil cp seed_ttt_policy.sql gs://scalpyn-mlflow/temp/
--   2. gcloud sql import sql scalpyndata gs://scalpyn-mlflow/temp/seed_ttt_policy.sql \
--        --database=postgres --quiet
--   3. gsutil rm gs://scalpyn-mlflow/temp/seed_ttt_policy.sql
--
-- Para ajustar os parâmetros TTT em produção, atualizar as env vars no
-- Cloud Run service 'scalpyn-worker-structural':
--   TTT_ENABLED=true
--   TTT_TP_PCT=1.0
--   TTT_TIMEOUT_MINUTES=180
--
-- Para desabilitar TTT temporariamente (sem redeploy):
--   UPDATE config_profiles
--      SET config_json = config_json || '{"ttt_enabled": false}'::jsonb
--    WHERE config_type = 'ttt_policy';
-- (O ttt_analyzer verifica este flag antes de processar cada shadow.)
--
-- NOTA: dry_run_mode=true por padrão — o ttt_analyzer respeita este flag
-- para não gravar ttt_outcome em produção até validação completa.
-- Para ativar labels em produção:
--   UPDATE config_profiles
--      SET config_json = config_json || '{"dry_run_mode": false}'::jsonb
--    WHERE config_type = 'ttt_policy';

INSERT INTO config_profiles (
    id,
    user_id,
    pool_id,
    config_type,
    config_json,
    is_active,
    created_at,
    updated_at
)
SELECT
    gen_random_uuid(),
    p.user_id,
    NULL,
    'ttt_policy',
    '{
        "ttt_enabled":             true,
        "ttt_tp_pct":              1.0,
        "ttt_timeout_minutes":     180,
        "ttt_early_exit_enabled":  false,
        "ttt_early_exit_minutes":  30,
        "ttt_early_exit_min_profit_pct": 0.1,
        "dry_run_mode":            true,
        "description": "Time-To-Target policy: FAST_WIN = +1% em <3h, TIMEOUT = nao atingiu"
    }'::jsonb,
    true,
    NOW(),
    NOW()
FROM profiles p
WHERE p.id = '29155eda-6d8f-4abf-9f58-b3999ba9c878'
ON CONFLICT DO NOTHING;
