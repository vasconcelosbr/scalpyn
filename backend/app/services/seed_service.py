import json
import logging
from uuid import UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from ..models.user import User
from ..models.config_profile import ConfigProfile
from .config_service import config_service
from .watchlist_performance_ranking_service import DEFAULT_RANKING_CONFIG

logger = logging.getLogger(__name__)

DEFAULT_INDICATORS = {
    # `period` (legado, único) preservado para retrocompat; `periods` (lista
    # opcional) ativa cálculo multi-período aditivo (rsi_6, rsi_12, rsi_24).
    "rsi": {"enabled": True, "period": 14, "periods": [6, 12, 24]},
    "adx": {"enabled": True, "period": 14},
    # EMA10 (micro, ≤21) e EMA30 (struct/hybrid, 22-49) adicionados.
    "ema": {"enabled": True, "periods": [5, 9, 10, 21, 30, 50, 200]},
    "atr": {"enabled": True, "period": 14},
    "macd": {"enabled": True, "fast": 12, "slow": 26, "signal": 9},
    "vwap": {"enabled": True, "reset_period": "daily"},
    "stochastic": {"enabled": True, "k": 14, "d": 3, "smooth": 3},
    "obv": {"enabled": True},
    # PSAR estendido: AF padrão Wilder + sistema anti-exaustão (RSI / distance)
    # + filtro ADX opcional. Todos os thresholds env-safe via update_config.
    "parabolic_sar": {
        "enabled": True,
        "step": 0.02,           # legado — alias de af_start (preservado)
        "max_step": 0.2,        # legado — alias de af_max (preservado)
        "af_start": 0.02,
        "af_increment": 0.02,
        "af_max": 0.20,
        # Anti-exaustão: bloqueia sinal BUY quando preço já correu demais ou RSI saturou
        "max_distance_pct": 3.0,
        "rsi_max": 75.0,
        # Filtro ADX (opt-in): só permite sinal quando tendência é forte
        "adx_filter_enabled": False,
        "adx_min_threshold": 20.0,
    },
    "bollinger": {"enabled": True, "period": 20, "deviation": 2.0},
    "zscore": {"enabled": False, "lookback": 20},
    # Robust indicators: when no real trade-flow data is available the
    # FeatureEngine returns None and the envelope tags the indicator as
    # NO_DATA. Candle-derived approximations are no longer supported.
    "volume_delta": {"enabled": True},
    "volume_metrics": {"enabled": True, "min_coverage_hours": 23.5},
    "volume_spike": {"enabled": True, "lookback": 20},
    "taker_ratio": {"enabled": True, "lookback": 20},
    "market_data_fallback": {
        "orderbook_depth_levels": 10,
        "ticker_cache_ttl_seconds": 5,
        "orderbook_cache_ttl_seconds": 5,
        "trades_cache_ttl_seconds": 1,
        "binance_trade_limit": 500,
        "max_cache_entries": 1000,
        "confidence_scores": {"gate": 0.7, "binance": 0.9, "mixed": 0.85},
    },
    "orderbook_imbalance": {"enabled": False, "depth_levels": 10},
    "funding_rate": {"enabled": True},
    "btc_dominance": {"enabled": False},
    # Entry Exhaustion Score — Fase 1 Shadow Mode (observacional, não bloqueia trades)
    # Computed by FeatureEngine._calc_entry_exhaustion() on 5m structural candles.
    # 0 = sem exaustão, 100 = máxima exaustão de entrada.
    "entry_exhaustion": {"enabled": True},
}

DEFAULT_SCORE = {
    "weights": {"liquidity": 25, "market_structure": 25, "momentum": 25, "signal": 25},
    "scoring_rules": [
        {"id": "rsi_1", "indicator": "rsi", "operator": "<=", "value": 25, "points": 40, "category": "momentum"},
        {"id": "rsi_2", "indicator": "rsi", "operator": "<=", "value": 30, "points": 30, "category": "momentum"},
        {"id": "ema_trend_1", "indicator": "ema_trend", "operator": "ema9>ema50>ema200", "value": None, "points": 30, "category": "market_structure"}
    ],
    "thresholds": {"strong_buy": 80, "buy": 65, "neutral": 40},
    "auto_select_top_n": 5,
    "auto_select_min_score": 80,
}

DEFAULT_SIGNAL = {
    "logic": "AND",
    "conditions": [
        {"id": "s1", "indicator": "alpha_score", "operator": ">", "value": 75, "required": True, "enabled": True},
        {"id": "s2", "indicator": "adx", "operator": ">", "value": 25, "required": True, "enabled": True}
    ]
}

DEFAULT_BLOCK = {
    "blocks": [
        {"id": "b1", "name": "RSI out of range", "enabled": True, "indicator": "rsi", "min": 20, "max": 80},
        {"id": "b2", "name": "Spread too high", "enabled": True, "indicator": "spread_pct", "operator": "<", "value": 0.3}
    ]
}

DEFAULT_RISK = {
    "take_profit_pct": 1.5,
    "stop_loss_atr_multiplier": 1.5,
    "trailing_stop_enabled": False,
    "max_positions": 5,
    "daily_loss_limit_pct": 3.0,
    "max_exposure_per_asset_pct": 20,
    "circuit_breaker_consecutive_losses": 3,
    "default_order_type": "limit",
    "max_slippage_pct": 0.1,
    "capital_per_trade_pct": 10,
    "max_capital_in_use_pct": 80
}

DEFAULT_STRATEGY = {
    "strategies": [
        {"id": "momentum_breakout", "name": "Momentum Breakout", "enabled": True, "params": {"volume_spike_multiplier": 2, "adx_min": 25, "lookback": 20}},
        {"id": "mean_reversion", "name": "Mean Reversion", "enabled": True, "params": {"rsi_threshold": 30, "bollinger_deviation": 2.0, "zscore_threshold": -2.0}}
    ]
}

DEFAULT_UNIVERSE = {
    "min_volume_24h": 5000000,
    "min_market_cap": 50000000,
    "accepted_pairs": ["USDT"],
    "accepted_exchanges": ["binance", "bybit", "okx", "gate"],
    "max_assets": 100,
    "refresh_interval_hours": 24
}

DEFAULT_DECISION_LOG = {
    "page_size": 50,
    "max_page_size": 200,
    "client_buffer_size": 200,
    "max_displayed_metrics": 16,
    "realtime_highlight_ms": 3000,
    "score_delta_threshold": 5.0,
    "direction_change_logs": True,
}

DEFAULT_AI_SETTINGS = {
    "ml_enabled": True,
    "model_path": "/tmp/scalpyn_models/model.pkl",
    "ai_block_threshold": 0.5,
    "use_ml_ranking": True,
    "fallback_probability": 1.0,
    "auto_retrain_enabled": False,
    "retrain_interval_days": 7,
    "min_simulations_for_training": 1000,
}

async def seed_user_defaults(db: AsyncSession, user_id: UUID) -> None:
    # Check if config exists
    query = select(ConfigProfile).where(ConfigProfile.user_id == user_id)
    existing = await db.execute(query)
    
    if existing.scalars().first() is None:
        logger.info(f"Seeding default configs for user {user_id}")
        await config_service.update_config(db, 'indicators', user_id, DEFAULT_INDICATORS, user_id, change_description="System Seed Reset")
        await config_service.update_config(db, 'score', user_id, DEFAULT_SCORE, user_id, change_description="System Seed Reset")
        await config_service.update_config(db, 'signal', user_id, DEFAULT_SIGNAL, user_id, change_description="System Seed Reset")
        await config_service.update_config(db, 'block', user_id, DEFAULT_BLOCK, user_id, change_description="System Seed Reset")
        await config_service.update_config(db, 'risk', user_id, DEFAULT_RISK, user_id, change_description="System Seed Reset")
        await config_service.update_config(db, 'strategy', user_id, DEFAULT_STRATEGY, user_id, change_description="System Seed Reset")
        await config_service.update_config(db, 'universe', user_id, DEFAULT_UNIVERSE, user_id, change_description="System Seed Reset")
        await config_service.update_config(db, 'decision_log', user_id, DEFAULT_DECISION_LOG, user_id, change_description="System Seed Reset")
        await config_service.update_config(db, 'ai-settings', user_id, DEFAULT_AI_SETTINGS, user_id, change_description="System Seed Reset")
        await config_service.update_config(db, 'watchlist_performance_ranking', user_id, DEFAULT_RANKING_CONFIG, user_id, change_description="System Seed Reset")
