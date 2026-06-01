-- Nova arquitetura ML — BLOCO A/B/C (PROMPT_ARQUITETURA_ML_SPOT)
-- Data: 2026-06-01
--
-- Estado inicial:
--   new_arch_capture_enabled   = true   → liga captura + shadow watchlist spot
--   new_arch_ml_scorer_enabled = false  → ML scorer desligado até dataset acumular
--   new_arch_l3_uses_ml_score  = false  → L3 continua com pipeline atual
--
-- Garantia: qualquer flag false → comportamento IDÊNTICO ao sistema atual.
-- Ativar ml_scorer e l3_uses apenas após validar separabilidade no dataset
-- WATCHLIST_SPOT (AUC > 0.6 no espectro completo).

INSERT INTO config_profiles (id, user_id, config_type, config_json)
SELECT
    gen_random_uuid(),
    u.id,
    'pool_config',
    '{
        "new_arch_capture_enabled":   true,
        "new_arch_ml_scorer_enabled": false,
        "new_arch_l3_uses_ml_score":  false,
        "shadow_watchlist_l1_spot_id": "9d7a9f34-45fd-44c3-97b2-4f4af2fe9d29",
        "ml_training_lookback_days":  30,
        "ml_target_type":             "binary",
        "pool_structural_filter": {
            "min_volume_24h_usdt":       1000000,
            "max_spread_pct":            0.5,
            "min_orderbook_depth_usdt":  50000
        }
    }'::jsonb
FROM users u
WHERE NOT EXISTS (
    SELECT 1 FROM config_profiles cp
    WHERE cp.user_id = u.id
      AND cp.config_type = 'pool_config'
)
ON CONFLICT DO NOTHING;

-- Para atualizar um row existente (caso já exista pool_config para o usuário):
-- UPDATE config_profiles
-- SET config_json = config_json || '{
--     "new_arch_capture_enabled":   true,
--     "new_arch_ml_scorer_enabled": false,
--     "new_arch_l3_uses_ml_score":  false,
--     "shadow_watchlist_l1_spot_id": "9d7a9f34-45fd-44c3-97b2-4f4af2fe9d29",
--     "ml_training_lookback_days":  30,
--     "ml_target_type":             "binary",
--     "pool_structural_filter": {
--         "min_volume_24h_usdt":       1000000,
--         "max_spread_pct":            0.5,
--         "min_orderbook_depth_usdt":  50000
--     }
-- }'::jsonb
-- WHERE config_type = 'pool_config';
