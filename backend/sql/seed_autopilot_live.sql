-- Seed Autopilot Guardrails — Ativa autopilot para L3 com autoridade total.
-- Execute no Railway shell: psql $DATABASE_URL -f seed_autopilot_live.sql
-- OU cole no Query Editor do Railway Postgres.

-- 1. Inserir/atualizar guardrails para ativar autopilot
INSERT INTO config_profiles (
    user_id,
    config_type,
    config_json,
    updated_at
)
SELECT
    -- Usa o primeiro user_id que tem auto_pilot_enabled=True
    cp.user_id,
    'autopilot_guardrails',
    jsonb_build_object(
        -- Ativar execução real (sai do DRY RUN)
        'dry_run_mode',                 false,
        -- Autoridade total: pode ajustar scoring_rules, block_rules, entry_triggers, minimum_score
        'autopilot_full_authority',      true,
        'autopilot_can_adjust',          jsonb_build_array(
            'scoring_rules', 'minimum_score', 'block_rules', 'entry_triggers'
        ),
        -- Janela mínima: 3 dias (suficiente para ~1000+ shadows)
        'min_span_days',                3,
        -- Limites de segurança
        'ev_min_threshold_pct',         -0.30,
        'fpr_max_threshold',            0.65,
        'selection_inversion_delta_pct', 0.50,
        'rule_max_delta_per_cycle',     1,
        'rule_points_min',              -10,
        'rule_points_max',              10,
        'min_samples_per_rule',         15,
        -- Circuit breaker: pausa após 3 regressões consecutivas
        'circuit_breaker_threshold',    3,
        'circuit_breaker_pause_hours',  168,
        'kill_switch',                  false,
        -- Minimum score clamps
        'minimum_score_floor',          0,
        'minimum_score_ceiling',        100,
        'min_score_delta_per_cycle',    1,
        -- Performance rollback (ativar após validação)
        'behavioral_cb_enabled',        false,
        'performance_rollback_enabled', false
    ),
    NOW()
FROM config_profiles cp
WHERE cp.auto_pilot_enabled = true
LIMIT 1
ON CONFLICT (user_id, config_type)
DO UPDATE SET
    config_json = EXCLUDED.config_json,
    updated_at = NOW();

-- 2. Verificar resultado
SELECT
    user_id,
    config_type,
    config_json->>'dry_run_mode' AS dry_run,
    config_json->>'autopilot_full_authority' AS full_authority,
    config_json->>'min_span_days' AS min_span_days,
    updated_at
FROM config_profiles
WHERE config_type = 'autopilot_guardrails';
