-- Seed: Autopilot Guardrails
-- Aplica os guardrails de segurança do Auto-Pilot no profile L3 alvo.
-- Executar UMA VEZ. Idempotente via ON CONFLICT DO NOTHING.
--
-- Aplicar via Railway CLI ou cliente psql direto:
--   railway connect Postgres  (depois: \i seed_autopilot_guardrails.sql)
--   ou: psql $DATABASE_URL -f seed_autopilot_guardrails.sql
--
-- NOTA: dry_run_mode=true por padrão — o autopilot SIMULA mas NÃO escreve config.
-- Para ativar escrita real (decisão separada do operador, após validação pós-deploy):
--   UPDATE config_profiles SET config_json = config_json ||
--     '{"dry_run_mode": false}'::jsonb WHERE config_type = 'autopilot_guardrails';
--
-- Campos adicionados nesta versão vs seed original:
--   minimum_score_floor, minimum_score_ceiling, min_score_delta_per_cycle,
--   autopilot_full_authority, autopilot_can_adjust (sem "filters" — stub não implementado)

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
        "minimum_score_floor":           0,
        "minimum_score_ceiling":         100,
        "min_score_delta_per_cycle":     1,
        "autopilot_full_authority":      false,
        "autopilot_can_adjust": ["scoring_rules","minimum_score","block_rules","entry_triggers"],
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
