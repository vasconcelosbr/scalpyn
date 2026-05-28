-- Seed: Autopilot Guardrails
-- Aplica os guardrails de segurança do Auto-Pilot no profile L3 alvo.
-- Executar UMA VEZ. Idempotente via ON CONFLICT DO NOTHING.
--
-- Passos para aplicar via gcloud:
--   1. gsutil cp seed_autopilot_guardrails.sql gs://scalpyn-mlflow/temp/
--   2. gcloud sql import sql scalpyndata gs://scalpyn-mlflow/temp/seed_autopilot_guardrails.sql \
--        --database=postgres --quiet
--   3. gsutil rm gs://scalpyn-mlflow/temp/seed_autopilot_guardrails.sql
--
-- NOTA: dry_run_mode=true por padrão — o autopilot SIMULA mas NÃO escreve config.
-- Para ativar escrita real: UPDATE config_profiles SET config_json = config_json ||
--   '{"dry_run_mode": false}'::jsonb WHERE config_type = 'autopilot_guardrails';

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
    'autopilot_guardrails',
    '{
        "ev_min_threshold_pct":           0.0,
        "fpr_max_threshold":              0.65,
        "selection_inversion_delta_pct":  0.50,
        "rule_max_delta_per_cycle":       1,
        "rule_points_min":               -10,
        "rule_points_max":               10,
        "weight_max_delta_per_cycle":     5,
        "threshold_max_delta_per_cycle":  2,
        "min_samples_per_rule":          15,
        "circuit_breaker_threshold":      3,
        "circuit_breaker_pause_hours":   168,
        "kill_switch":                   false,
        "dry_run_mode":                  true,
        "scope_profile_id":              "29155eda-6d8f-4abf-9f58-b3999ba9c878"
    }'::jsonb,
    true,
    NOW(),
    NOW()
FROM profiles p
WHERE p.id = '29155eda-6d8f-4abf-9f58-b3999ba9c878'
ON CONFLICT DO NOTHING;
