-- ═══════════════════════════════════════════════════════════════════════════
-- ATIVAR AUTOPILOT — Sai do DRY_RUN e habilita mutações na L3
-- ═══════════════════════════════════════════════════════════════════════════
-- Cole este SQL no Railway Postgres → Query Editor
-- OU execute: psql $DATABASE_URL -f seed_autopilot_live.sql
-- ═══════════════════════════════════════════════════════════════════════════

-- 1. Verificar se já existe registro de guardrails
SELECT id, user_id, config_type, config_json->>'dry_run_mode' AS dry_run
FROM config_profiles
WHERE config_type = 'autopilot_guardrails';

-- 2. Se NÃO existir (resultado vazio acima), INSERIR:
-- (Substitua o user_id pelo seu UUID — pega do registro existente do autopilot)
INSERT INTO config_profiles (user_id, config_type, config_json)
SELECT
    cp.user_id,
    'autopilot_guardrails',
    '{
        "dry_run_mode": false,
        "autopilot_full_authority": true,
        "autopilot_can_adjust": ["scoring_rules", "minimum_score", "block_rules", "entry_triggers"],
        "min_span_days": 3,
        "ev_min_threshold_pct": -0.30,
        "fpr_max_threshold": 0.65,
        "selection_inversion_delta_pct": 0.50,
        "rule_max_delta_per_cycle": 1,
        "rule_points_min": -10,
        "rule_points_max": 10,
        "min_samples_per_rule": 15,
        "circuit_breaker_threshold": 3,
        "circuit_breaker_pause_hours": 168,
        "kill_switch": false,
        "minimum_score_floor": 0,
        "minimum_score_ceiling": 100,
        "min_score_delta_per_cycle": 1,
        "behavioral_cb_enabled": false,
        "performance_rollback_enabled": false
    }'::jsonb
FROM config_profiles cp
WHERE cp.config_type IN ('signal', 'block', 'filters')
GROUP BY cp.user_id
LIMIT 1;

-- 3. Se JÁ existir, ATUALIZAR para sair do dry_run:
UPDATE config_profiles
SET config_json = config_json
    || '{"dry_run_mode": false}'::jsonb
    || '{"autopilot_full_authority": true}'::jsonb
    || '{"min_span_days": 3}'::jsonb
    || '{"autopilot_can_adjust": ["scoring_rules", "minimum_score", "block_rules", "entry_triggers"]}'::jsonb,
    updated_at = NOW()
WHERE config_type = 'autopilot_guardrails';

-- 4. Confirmar resultado
SELECT
    user_id,
    config_json->>'dry_run_mode' AS dry_run,
    config_json->>'autopilot_full_authority' AS full_authority,
    config_json->>'min_span_days' AS min_span_days,
    config_json->'autopilot_can_adjust' AS can_adjust,
    updated_at
FROM config_profiles
WHERE config_type = 'autopilot_guardrails';
