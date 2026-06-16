-- Strategy Lab Profiles — 10 initial profiles for testing
-- Each profile represents a different signal hypothesis evaluated in parallel.
-- Run once: psql $DATABASE_URL < seed_strategy_lab_profiles.sql
-- These profiles are used by pipeline_scan to capture multi-strategy shadows
-- via create_strategy_lab_shadows / create_strategy_lab_rejected_shadows.
-- All start with is_active=true so they are picked up by the next scan cycle.

DO $$
DECLARE
    _user_id UUID;
BEGIN
    SELECT id INTO _user_id FROM users LIMIT 1;

    IF _user_id IS NULL THEN
        RAISE EXCEPTION 'No users found in database — run after user creation';
    END IF;

    -- Profile 1: L3_TREND_CONSERVADOR_V1
    INSERT INTO profiles (id, user_id, name, description, is_active, config, profile_role,
                          pipeline_order, pipeline_label, auto_pilot_enabled, created_at, updated_at)
    VALUES (gen_random_uuid(), _user_id, 'L3_TREND_CONSERVADOR_V1',
            'Tendência conservadora — ADX forte + RSI médio', true,
        '{"default_timeframe": "5m", "filters": {"logic": "AND", "conditions": []},
          "scoring": {"enabled": true, "weights": {}},
          "signals": {"logic": "AND", "conditions": [
            {"type": "threshold", "field": "adx", "operator": ">=", "value": 25},
            {"type": "threshold", "field": "rsi", "operator": "between", "value": [45, 65]}
          ]},
          "block_rules": {"blocks": []},
          "entry_triggers": {"logic": "AND", "conditions": []}}'::jsonb,
        'primary_filter', '3', 'L3 Trend Conservador', false, now(), now())
    ON CONFLICT DO NOTHING;

    -- Profile 2: L3_TREND_FORTE_V1
    INSERT INTO profiles (id, user_id, name, description, is_active, config, profile_role,
                          pipeline_order, pipeline_label, auto_pilot_enabled, created_at, updated_at)
    VALUES (gen_random_uuid(), _user_id, 'L3_TREND_FORTE_V1',
            'Tendência forte — ADX muito alto + EMA alinhado', true,
        '{"default_timeframe": "5m", "filters": {"logic": "AND", "conditions": []},
          "scoring": {"enabled": true, "weights": {}},
          "signals": {"logic": "AND", "conditions": [
            {"type": "threshold", "field": "adx", "operator": ">=", "value": 35},
            {"type": "boolean", "field": "ema9_gt_ema21", "operator": "is_true"},
            {"type": "boolean", "field": "ema50_gt_ema200", "operator": "is_true"}
          ]},
          "block_rules": {"blocks": []},
          "entry_triggers": {"logic": "AND", "conditions": []}}'::jsonb,
        'primary_filter', '3', 'L3 Trend Forte', false, now(), now())
    ON CONFLICT DO NOTHING;

    -- Profile 3: L3_BREAKOUT_V1
    INSERT INTO profiles (id, user_id, name, description, is_active, config, profile_role,
                          pipeline_order, pipeline_label, auto_pilot_enabled, created_at, updated_at)
    VALUES (gen_random_uuid(), _user_id, 'L3_BREAKOUT_V1',
            'Breakout — volume spike + BB comprimido expandindo', true,
        '{"default_timeframe": "5m", "filters": {"logic": "AND", "conditions": []},
          "scoring": {"enabled": true, "weights": {}},
          "signals": {"logic": "AND", "conditions": [
            {"type": "threshold", "field": "volume_spike", "operator": ">=", "value": 2.0},
            {"type": "threshold", "field": "bb_width", "operator": ">=", "value": 0.02}
          ]},
          "block_rules": {"blocks": []},
          "entry_triggers": {"logic": "AND", "conditions": []}}'::jsonb,
        'primary_filter', '3', 'L3 Breakout', false, now(), now())
    ON CONFLICT DO NOTHING;

    -- Profile 4: L3_PULLBACK_TENDENCIA_V1
    INSERT INTO profiles (id, user_id, name, description, is_active, config, profile_role,
                          pipeline_order, pipeline_label, auto_pilot_enabled, created_at, updated_at)
    VALUES (gen_random_uuid(), _user_id, 'L3_PULLBACK_TENDENCIA_V1',
            'Pullback em tendência — RSI baixo em uptrend EMA200', true,
        '{"default_timeframe": "5m", "filters": {"logic": "AND", "conditions": []},
          "scoring": {"enabled": true, "weights": {}},
          "signals": {"logic": "AND", "conditions": [
            {"type": "boolean", "field": "ema50_gt_ema200", "operator": "is_true"},
            {"type": "threshold", "field": "rsi", "operator": "<=", "value": 45},
            {"type": "threshold", "field": "rsi", "operator": ">=", "value": 30}
          ]},
          "block_rules": {"blocks": []},
          "entry_triggers": {"logic": "AND", "conditions": []}}'::jsonb,
        'primary_filter', '3', 'L3 Pullback Tendência', false, now(), now())
    ON CONFLICT DO NOTHING;

    -- Profile 5: L3_MEAN_REVERSION_CONTROLADO_V1
    INSERT INTO profiles (id, user_id, name, description, is_active, config, profile_role,
                          pipeline_order, pipeline_label, auto_pilot_enabled, created_at, updated_at)
    VALUES (gen_random_uuid(), _user_id, 'L3_MEAN_REVERSION_CONTROLADO_V1',
            'Mean reversion controlado — RSI sobrevendido + VWAP próximo', true,
        '{"default_timeframe": "5m", "filters": {"logic": "AND", "conditions": []},
          "scoring": {"enabled": true, "weights": {}},
          "signals": {"logic": "AND", "conditions": [
            {"type": "threshold", "field": "rsi", "operator": "<=", "value": 35},
            {"type": "threshold", "field": "vwap_distance_pct", "operator": ">=", "value": -3.0}
          ]},
          "block_rules": {"blocks": []},
          "entry_triggers": {"logic": "AND", "conditions": []}}'::jsonb,
        'primary_filter', '3', 'L3 Mean Reversion', false, now(), now())
    ON CONFLICT DO NOTHING;

    -- Profile 6: L3_MOMENTUM_INICIAL_V1
    INSERT INTO profiles (id, user_id, name, description, is_active, config, profile_role,
                          pipeline_order, pipeline_label, auto_pilot_enabled, created_at, updated_at)
    VALUES (gen_random_uuid(), _user_id, 'L3_MOMENTUM_INICIAL_V1',
            'Momentum inicial — MACD cruzando + taker ratio elevado', true,
        '{"default_timeframe": "5m", "filters": {"logic": "AND", "conditions": []},
          "scoring": {"enabled": true, "weights": {}},
          "signals": {"logic": "AND", "conditions": [
            {"type": "threshold", "field": "macd_histogram_pct", "operator": ">=", "value": 0.01},
            {"type": "threshold", "field": "taker_ratio", "operator": ">=", "value": 0.55}
          ]},
          "block_rules": {"blocks": []},
          "entry_triggers": {"logic": "AND", "conditions": []}}'::jsonb,
        'primary_filter', '3', 'L3 Momentum Inicial', false, now(), now())
    ON CONFLICT DO NOTHING;

    -- Profile 7: L3_HIGH_LIQUIDITY_V1
    INSERT INTO profiles (id, user_id, name, description, is_active, config, profile_role,
                          pipeline_order, pipeline_label, auto_pilot_enabled, created_at, updated_at)
    VALUES (gen_random_uuid(), _user_id, 'L3_HIGH_LIQUIDITY_V1',
            'Alta liquidez — volume 24h alto + spread baixo', true,
        '{"default_timeframe": "5m",
          "filters": {"logic": "AND", "conditions": [
            {"type": "threshold", "field": "volume_24h_usdt", "operator": ">=", "value": 50000000}
          ]},
          "scoring": {"enabled": true, "weights": {}},
          "signals": {"logic": "AND", "conditions": [
            {"type": "threshold", "field": "spread_pct", "operator": "<=", "value": 0.05}
          ]},
          "block_rules": {"blocks": []},
          "entry_triggers": {"logic": "AND", "conditions": []}}'::jsonb,
        'primary_filter', '3', 'L3 High Liquidity', false, now(), now())
    ON CONFLICT DO NOTHING;

    -- Profile 8: L3_ANTI_EXAUSTAO_V1
    INSERT INTO profiles (id, user_id, name, description, is_active, config, profile_role,
                          pipeline_order, pipeline_label, auto_pilot_enabled, created_at, updated_at)
    VALUES (gen_random_uuid(), _user_id, 'L3_ANTI_EXAUSTAO_V1',
            'Anti-exaustão — RSI não sobrecomprado + ADX crescendo', true,
        '{"default_timeframe": "5m", "filters": {"logic": "AND", "conditions": []},
          "scoring": {"enabled": true, "weights": {}},
          "signals": {"logic": "AND", "conditions": [
            {"type": "threshold", "field": "rsi", "operator": "<=", "value": 70},
            {"type": "threshold", "field": "adx_acceleration", "operator": ">=", "value": 0}
          ]},
          "block_rules": {"blocks": []},
          "entry_triggers": {"logic": "AND", "conditions": []}}'::jsonb,
        'primary_filter', '3', 'L3 Anti-Exaustão', false, now(), now())
    ON CONFLICT DO NOTHING;

    -- Profile 9: L3_VOLATILIDADE_MODERADA_V1
    INSERT INTO profiles (id, user_id, name, description, is_active, config, profile_role,
                          pipeline_order, pipeline_label, auto_pilot_enabled, created_at, updated_at)
    VALUES (gen_random_uuid(), _user_id, 'L3_VOLATILIDADE_MODERADA_V1',
            'Volatilidade moderada — ATR% médio + BB width médio', true,
        '{"default_timeframe": "5m", "filters": {"logic": "AND", "conditions": []},
          "scoring": {"enabled": true, "weights": {}},
          "signals": {"logic": "AND", "conditions": [
            {"type": "threshold", "field": "atr_pct", "operator": ">=", "value": 0.5},
            {"type": "threshold", "field": "atr_pct", "operator": "<=", "value": 2.5},
            {"type": "threshold", "field": "bb_width", "operator": ">=", "value": 0.015}
          ]},
          "block_rules": {"blocks": []},
          "entry_triggers": {"logic": "AND", "conditions": []}}'::jsonb,
        'primary_filter', '3', 'L3 Volatilidade Moderada', false, now(), now())
    ON CONFLICT DO NOTHING;

    -- Profile 10: L3_ML_PRIORITY_V1
    INSERT INTO profiles (id, user_id, name, description, is_active, config, profile_role,
                          pipeline_order, pipeline_label, auto_pilot_enabled, created_at, updated_at)
    VALUES (gen_random_uuid(), _user_id, 'L3_ML_PRIORITY_V1',
            'ML Priority — captura ampla para maximizar dataset ML', true,
        '{"default_timeframe": "5m", "filters": {"logic": "AND", "conditions": []},
          "scoring": {"enabled": true, "weights": {}},
          "signals": {"logic": "AND", "conditions": [
            {"type": "threshold", "field": "adx", "operator": ">=", "value": 15}
          ]},
          "block_rules": {"blocks": []},
          "entry_triggers": {"logic": "AND", "conditions": []}}'::jsonb,
        'primary_filter', '3', 'L3 ML Priority', false, now(), now())
    ON CONFLICT DO NOTHING;

    RAISE NOTICE 'Strategy Lab profiles seeded for user_id=%', _user_id;
END $$;
