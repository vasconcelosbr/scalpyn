-- Seed: ML Config Profile
-- Cria o config_profile de tipo 'ml' com parâmetros de fee e label para o pipeline de treino.
-- Executar UMA VEZ. Idempotente via INSERT ... WHERE NOT EXISTS.
--
-- Passos para aplicar no Railway:
--   railway connect Postgres
--   \i /path/to/seed_ml_config.sql
--
-- OU via railway run (instalar psql primeiro):
--   railway run --service Postgres -- psql $DATABASE_URL -f seed_ml_config.sql
--
-- Verificar após aplicação:
--   SELECT id, user_id, config_type, config_json FROM config_profiles WHERE config_type = 'ml';

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
    '8080110c-ee9d-4a2b-a53f-6bef86dd8867',
    NULL,
    'ml',
    '{
        "ml_fee_roundtrip_pct":         0.20,
        "ml_label_net_of_fees":         true,
        "ml_win_fast_threshold_seconds": 1800,
        "shadow_barrier_mode":          "FIXED",
        "shadow_atr_multiplier_tp":     1.5,
        "shadow_atr_multiplier_sl":     1.5,
        "shadow_atr_period":            14,
        "shadow_atr_timeframe":         "5m",
        "shadow_barrier_min_pct":       0.5,
        "shadow_barrier_max_pct":       3.0
    }'::jsonb,
    true,
    NOW(),
    NOW()
WHERE NOT EXISTS (
    SELECT 1 FROM config_profiles
    WHERE user_id = '8080110c-ee9d-4a2b-a53f-6bef86dd8867'
      AND config_type = 'ml'
);
