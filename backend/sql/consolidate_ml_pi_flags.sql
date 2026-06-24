-- Fase 3 (Profile Intelligence Adaptive Loop reformulation, audit 2026-06-24).
--
-- enable_catboost / enable_lightgbm existem hoje em DOIS config_type:
--   - config_type='profile_intelligence'  -> ÚNICO lugar realmente lido em runtime
--     (backend/app/tasks/profile_intelligence_job.py:148-157,
--      _run_ml_challengers_if_enabled). Valor atual: false/false
--      (desativado em 2026-06-23 após v41/v42 falharem o gate de teste).
--   - config_type='ml'                    -> cópia morta, NUNCA lida por
--     nenhum código em backend/app/** (confirmado por busca completa).
--     Valor atual: true/true — divergente e enganoso para quem inspeciona
--     a config 'ml' assumindo que ela é a fonte de verdade operacional.
--
-- Esta correção REMOVE as duas chaves mortas de config_type='ml' (não apaga
-- a linha, não apaga nenhum outro dado — apenas as duas chaves não-lidas).
-- profile_intelligence passa a ser a ÚNICA fonte de verdade para estes flags.
-- Idempotente: reexecutar é seguro (operador jsonb '-' em chave já ausente é no-op).
--
-- Verificar antes:
--   SELECT config_json->'enable_catboost', config_json->'enable_lightgbm'
--   FROM config_profiles WHERE config_type = 'ml';
--
-- Executar:
--   psql $DATABASE_PUBLIC_URL -f consolidate_ml_pi_flags.sql
--
-- Verificar depois (deve retornar NULL para as duas colunas):
--   SELECT config_json->'enable_catboost', config_json->'enable_lightgbm'
--   FROM config_profiles WHERE config_type = 'ml';

UPDATE config_profiles
SET
    config_json = (config_json - 'enable_catboost') - 'enable_lightgbm',
    updated_at  = NOW()
WHERE config_type = 'ml'
  AND (config_json ? 'enable_catboost' OR config_json ? 'enable_lightgbm');
