-- =============================================================================
-- Seed Skill Profiles for existing users
-- Run this AFTER add_skills_engine.sql migration
-- =============================================================================

-- This script seeds the default skill profiles for ALL active users.
-- Each user gets 5 skills: mean_reversion, trend_following, breakout_hunter,
-- scalping, swing_trading.
--
-- The AI Adaptive mode doesn't need a DB record — it dynamically selects
-- from the available skills.

DO $$
DECLARE
    _user_id UUID;
BEGIN
    FOR _user_id IN
        SELECT DISTINCT user_id FROM profiles WHERE auto_pilot_enabled = true
    LOOP
        -- Mean Reversion
        INSERT INTO skill_profiles (user_id, skill_key, name, description, config, regime_affinity, is_default)
        VALUES (
            _user_id, 'mean_reversion', 'Mean Reversion',
            'Compra correções e retornos à média. Ideal em mercados laterais.',
            '{
                "scoring_rules": [
                    {"indicator": "rsi", "operator": "<", "value": 40, "points": 20, "category": "momentum", "label": "RSI em zona de sobrevenda"},
                    {"indicator": "stoch_k", "operator": "<", "value": 30, "points": 15, "category": "momentum", "label": "Stochastic em sobrevenda"},
                    {"indicator": "adx", "operator": ">", "value": 15, "points": 10, "category": "market_structure", "label": "ADX mínimo"},
                    {"indicator": "adx", "operator": "<", "value": 30, "points": 5, "category": "market_structure", "label": "ADX não muito forte"},
                    {"indicator": "volume_spike", "operator": ">", "value": 1.2, "points": 10, "category": "liquidity", "label": "Volume acima da média"},
                    {"indicator": "zscore", "operator": "<", "value": -1.0, "points": 10, "category": "signal", "label": "Z-Score negativo"},
                    {"indicator": "rsi", "operator": ">", "value": 70, "points": -15, "category": "momentum", "label": "RSI alto penaliza"},
                    {"indicator": "adx", "operator": ">", "value": 40, "points": -10, "category": "market_structure", "label": "Tendência forte demais"}
                ],
                "scoring_thresholds": {"strong_buy": 75, "buy": 55, "neutral": 35},
                "block_rules": [
                    {"name": "Liquidez Insuficiente", "indicator": "volume_24h", "operator": "<", "value": 300000, "block_type": "risk"},
                    {"name": "Spread Proibitivo", "indicator": "spread_pct", "operator": ">", "value": 0.5, "block_type": "risk"}
                ]
            }'::jsonb,
            '["SIDEWAYS", "LOW_VOLATILITY"]'::jsonb,
            true
        )
        ON CONFLICT (user_id, skill_key) WHERE is_active = true DO NOTHING;

        -- Trend Following
        INSERT INTO skill_profiles (user_id, skill_key, name, description, config, regime_affinity, is_default)
        VALUES (
            _user_id, 'trend_following', 'Trend Following',
            'Segue tendências estabelecidas. RSI alto é POSITIVO.',
            '{
                "scoring_rules": [
                    {"indicator": "adx", "operator": ">", "value": 25, "points": 20, "category": "market_structure", "label": "Tendência forte (ADX > 25)"},
                    {"indicator": "adx", "operator": ">", "value": 35, "points": 10, "category": "market_structure", "label": "Tendência muito forte"},
                    {"indicator": "ema_full_alignment", "operator": "=", "value": true, "points": 20, "category": "market_structure", "label": "EMAs alinhadas"},
                    {"indicator": "macd_value", "operator": ">", "value": 0, "points": 15, "category": "momentum", "label": "MACD positivo"},
                    {"indicator": "macd_histogram", "operator": ">", "value": 0, "points": 10, "category": "momentum", "label": "MACD acelerando"},
                    {"indicator": "rsi", "operator": ">", "value": 50, "points": 10, "category": "momentum", "label": "RSI confirma força"},
                    {"indicator": "rsi", "operator": ">", "value": 60, "points": 5, "category": "momentum", "label": "RSI forte positivo"},
                    {"indicator": "volume_spike", "operator": ">", "value": 1.3, "points": 15, "category": "liquidity", "label": "Volume crescente"},
                    {"indicator": "rsi", "operator": ">", "value": 85, "points": -10, "category": "momentum", "label": "RSI exaustão"},
                    {"indicator": "adx", "operator": "<", "value": 15, "points": -20, "category": "market_structure", "label": "Sem tendência"},
                    {"indicator": "macd_value", "operator": "<", "value": 0, "points": -15, "category": "momentum", "label": "MACD contra tendência"}
                ],
                "scoring_thresholds": {"strong_buy": 70, "buy": 50, "neutral": 30},
                "block_rules": [
                    {"name": "Liquidez Mínima", "indicator": "volume_24h", "operator": "<", "value": 500000, "block_type": "risk"}
                ]
            }'::jsonb,
            '["TRENDING_BULL"]'::jsonb,
            true
        )
        ON CONFLICT (user_id, skill_key) WHERE is_active = true DO NOTHING;

        -- Breakout Hunter
        INSERT INTO skill_profiles (user_id, skill_key, name, description, config, regime_affinity, is_default)
        VALUES (
            _user_id, 'breakout_hunter', 'Breakout Hunter',
            'Captura rompimentos. Volume e ATR são mais importantes que RSI.',
            '{
                "scoring_rules": [
                    {"indicator": "volume_spike", "operator": ">", "value": 2.0, "points": 25, "category": "liquidity", "label": "Volume > 2x média"},
                    {"indicator": "volume_spike", "operator": ">", "value": 3.0, "points": 10, "category": "liquidity", "label": "Volume explosivo"},
                    {"indicator": "adx", "operator": ">", "value": 20, "points": 15, "category": "market_structure", "label": "Tendência emergente"},
                    {"indicator": "macd_histogram", "operator": ">", "value": 0, "points": 15, "category": "momentum", "label": "MACD acelerando"},
                    {"indicator": "rsi", "operator": "<", "value": 80, "points": 10, "category": "momentum", "label": "RSI não extremo"},
                    {"indicator": "atr_pct", "operator": ">", "value": 2.0, "points": 10, "category": "signal", "label": "ATR expandindo"},
                    {"indicator": "volume_spike", "operator": "<", "value": 1.0, "points": -20, "category": "liquidity", "label": "Sem volume"},
                    {"indicator": "adx", "operator": "<", "value": 10, "points": -15, "category": "market_structure", "label": "Sem direção"}
                ],
                "scoring_thresholds": {"strong_buy": 65, "buy": 45, "neutral": 25},
                "block_rules": [
                    {"name": "Volume Insuficiente", "indicator": "volume_24h", "operator": "<", "value": 500000, "block_type": "risk"}
                ]
            }'::jsonb,
            '["BREAKOUT"]'::jsonb,
            true
        )
        ON CONFLICT (user_id, skill_key) WHERE is_active = true DO NOTHING;

        -- Scalping
        INSERT INTO skill_profiles (user_id, skill_key, name, description, config, regime_affinity, is_default)
        VALUES (
            _user_id, 'scalping', 'Scalping',
            'Movimentos curtos. Spread baixo, volume alto, momentum imediato.',
            '{
                "scoring_rules": [
                    {"indicator": "spread_pct", "operator": "<", "value": 0.05, "points": 20, "category": "liquidity", "label": "Spread apertado"},
                    {"indicator": "spread_pct", "operator": "<", "value": 0.1, "points": 10, "category": "liquidity", "label": "Spread aceitável"},
                    {"indicator": "volume_24h", "operator": ">", "value": 2000000, "points": 15, "category": "liquidity", "label": "Volume alto"},
                    {"indicator": "taker_ratio", "operator": ">", "value": 0.52, "points": 15, "category": "signal", "label": "Pressão compradora"},
                    {"indicator": "stoch_k", "operator": "<", "value": 35, "points": 10, "category": "momentum", "label": "Stochastic entrada"},
                    {"indicator": "atr_pct", "operator": ">", "value": 0.5, "points": 10, "category": "signal", "label": "Vol mínima"},
                    {"indicator": "spread_pct", "operator": ">", "value": 0.2, "points": -25, "category": "liquidity", "label": "Spread alto"},
                    {"indicator": "volume_24h", "operator": "<", "value": 500000, "points": -15, "category": "liquidity", "label": "Volume baixo"}
                ],
                "scoring_thresholds": {"strong_buy": 75, "buy": 55, "neutral": 35},
                "block_rules": [
                    {"name": "Spread Proibitivo", "indicator": "spread_pct", "operator": ">", "value": 0.3, "block_type": "risk"},
                    {"name": "Volume Mínimo", "indicator": "volume_24h", "operator": "<", "value": 1000000, "block_type": "risk"}
                ]
            }'::jsonb,
            '["HIGH_VOLATILITY"]'::jsonb,
            true
        )
        ON CONFLICT (user_id, skill_key) WHERE is_active = true DO NOTHING;

        -- Swing Trading
        INSERT INTO skill_profiles (user_id, skill_key, name, description, config, regime_affinity, is_default)
        VALUES (
            _user_id, 'swing_trading', 'Swing Trading',
            'Movimentos de vários dias. Tendência macro alinhada.',
            '{
                "scoring_rules": [
                    {"indicator": "ema_full_alignment", "operator": "=", "value": true, "points": 20, "category": "market_structure", "label": "EMAs alinhadas"},
                    {"indicator": "adx", "operator": ">", "value": 20, "points": 15, "category": "market_structure", "label": "Tendência presente"},
                    {"indicator": "rsi", "operator": ">", "value": 40, "points": 10, "category": "momentum", "label": "RSI neutro+"},
                    {"indicator": "rsi", "operator": "<", "value": 65, "points": 10, "category": "momentum", "label": "RSI não sobrecomprado"},
                    {"indicator": "macd_value", "operator": ">", "value": 0, "points": 10, "category": "momentum", "label": "MACD positivo"},
                    {"indicator": "volume_24h", "operator": ">", "value": 500000, "points": 10, "category": "liquidity", "label": "Volume adequado"},
                    {"indicator": "rsi", "operator": ">", "value": 80, "points": -15, "category": "momentum", "label": "RSI extremo"},
                    {"indicator": "adx", "operator": "<", "value": 12, "points": -10, "category": "market_structure", "label": "Sem tendência"}
                ],
                "scoring_thresholds": {"strong_buy": 70, "buy": 50, "neutral": 30},
                "block_rules": [
                    {"name": "Volume Mínimo", "indicator": "volume_24h", "operator": "<", "value": 300000, "block_type": "risk"}
                ]
            }'::jsonb,
            '["LOW_VOLATILITY", "TRENDING_BULL"]'::jsonb,
            true
        )
        ON CONFLICT (user_id, skill_key) WHERE is_active = true DO NOTHING;

        -- Default Regime-Skill Mapping
        INSERT INTO regime_skill_mapping (user_id, regime, primary_skill_id, secondary_skill_id)
        SELECT _user_id, 'SIDEWAYS',
               (SELECT id FROM skill_profiles WHERE user_id = _user_id AND skill_key = 'mean_reversion' AND is_active LIMIT 1),
               (SELECT id FROM skill_profiles WHERE user_id = _user_id AND skill_key = 'scalping' AND is_active LIMIT 1)
        ON CONFLICT (user_id, regime) DO NOTHING;

        INSERT INTO regime_skill_mapping (user_id, regime, primary_skill_id, secondary_skill_id)
        SELECT _user_id, 'TRENDING_BULL',
               (SELECT id FROM skill_profiles WHERE user_id = _user_id AND skill_key = 'trend_following' AND is_active LIMIT 1),
               (SELECT id FROM skill_profiles WHERE user_id = _user_id AND skill_key = 'breakout_hunter' AND is_active LIMIT 1)
        ON CONFLICT (user_id, regime) DO NOTHING;

        INSERT INTO regime_skill_mapping (user_id, regime, primary_skill_id, secondary_skill_id)
        SELECT _user_id, 'TRENDING_BEAR',
               (SELECT id FROM skill_profiles WHERE user_id = _user_id AND skill_key = 'swing_trading' AND is_active LIMIT 1),
               NULL
        ON CONFLICT (user_id, regime) DO NOTHING;

        INSERT INTO regime_skill_mapping (user_id, regime, primary_skill_id, secondary_skill_id)
        SELECT _user_id, 'BREAKOUT',
               (SELECT id FROM skill_profiles WHERE user_id = _user_id AND skill_key = 'breakout_hunter' AND is_active LIMIT 1),
               (SELECT id FROM skill_profiles WHERE user_id = _user_id AND skill_key = 'trend_following' AND is_active LIMIT 1)
        ON CONFLICT (user_id, regime) DO NOTHING;

        INSERT INTO regime_skill_mapping (user_id, regime, primary_skill_id, secondary_skill_id)
        SELECT _user_id, 'HIGH_VOLATILITY',
               (SELECT id FROM skill_profiles WHERE user_id = _user_id AND skill_key = 'scalping' AND is_active LIMIT 1),
               (SELECT id FROM skill_profiles WHERE user_id = _user_id AND skill_key = 'mean_reversion' AND is_active LIMIT 1)
        ON CONFLICT (user_id, regime) DO NOTHING;

        INSERT INTO regime_skill_mapping (user_id, regime, primary_skill_id, secondary_skill_id)
        SELECT _user_id, 'LOW_VOLATILITY',
               (SELECT id FROM skill_profiles WHERE user_id = _user_id AND skill_key = 'swing_trading' AND is_active LIMIT 1),
               (SELECT id FROM skill_profiles WHERE user_id = _user_id AND skill_key = 'mean_reversion' AND is_active LIMIT 1)
        ON CONFLICT (user_id, regime) DO NOTHING;

        RAISE NOTICE 'Seeded skills for user %', _user_id;
    END LOOP;
END $$;
