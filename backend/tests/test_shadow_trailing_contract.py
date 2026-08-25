from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.models.shadow_trade import ShadowTrade
from app.schemas.spot_engine_config import SpotEngineConfig
from app.services.shadow_trade_service import (
    SHADOW_TRAILING_CONTRACT_VERSION,
    _load_point_in_time_atr_pct,
    _shadow_user_config_from_spot_config,
    record_as_simulation,
)
from app.tasks import shadow_trade_monitor
from app.tasks.shadow_trade_monitor import (
    _advance_shadow,
    _resolve_trailing_stop_price,
)


ENTRY_AT = datetime(2026, 8, 25, 4, 44, 44, tzinfo=timezone.utc)


def _trailing_snapshot(activation=1.5, distance=1.0):
    return {
        "trailing": {
            "enabled": True,
            "activation_profit_pct": activation,
            "hwm_trail_pct": distance,
            "never_sell_at_loss": True,
            "min_profit_pct": 0.6,
            "safety_margin_above_entry_pct": 0.0,
            "contract_version": SHADOW_TRAILING_CONTRACT_VERSION,
        }
    }


def _shadow(config_snapshot=None):
    shadow = ShadowTrade()
    shadow.id = uuid4()
    shadow.symbol = "INJ_USDT"
    shadow.status = "RUNNING"
    shadow.amount_usdt = 1000.0
    shadow.entry_price = 100.0
    shadow.entry_timestamp = ENTRY_AT
    shadow.tp_price = 110.0
    shadow.sl_price = 90.0
    shadow.tp_pct = 10.0
    shadow.sl_pct = 10.0
    shadow.timeout_candles = 1440
    shadow.barrier_mode = "ATR_DYNAMIC"
    shadow.tp_pct_applied = 10.0
    shadow.sl_pct_applied = 10.0
    shadow.atr_pct_at_entry = 0.6
    shadow.min_price_post_entry = None
    shadow.max_price_post_entry = None
    shadow.mae_at = None
    shadow.mfe_at = None
    shadow.last_processed_time = ENTRY_AT
    shadow.created_at = ENTRY_AT
    shadow.ttt_enabled = False
    shadow.ttt_tp_pct = None
    shadow.config_snapshot = config_snapshot or {}
    shadow.eligible_for_training = True
    return shadow


def test_spot_config_is_frozen_into_shadow_trailing_policy():
    config = SpotEngineConfig.from_config_json(
        {
            "selling": {
                "min_profit_pct": 0.6,
                "safety_margin_above_entry_pct": 0.0,
                "never_sell_at_loss": True,
            },
            "sell_flow": {
                "trailing": {
                    "enabled": True,
                    "activation_profit_pct": 1.5,
                    "hwm_trail_pct": 1.0,
                }
            },
        }
    )

    policy = _shadow_user_config_from_spot_config(config)["trailing"]

    assert policy == _trailing_snapshot()["trailing"]


def test_trailing_arms_from_persisted_hwm_not_current_profit():
    shadow = _shadow(_trailing_snapshot())

    assert _resolve_trailing_stop_price(shadow, 100.0, 101.49) is None
    assert _resolve_trailing_stop_price(shadow, 100.0, 102.2) == pytest.approx(
        101.178
    )


def test_historical_three_percent_activation_does_not_arm_at_trade_mfe():
    shadow = _shadow(_trailing_snapshot(activation=3.0, distance=1.0))

    assert _resolve_trailing_stop_price(shadow, 100.0, 102.1574245744) is None


@pytest.mark.asyncio
async def test_trailing_closes_on_candle_after_hwm_activation(monkeypatch):
    shadow = _shadow(_trailing_snapshot())
    candles = [
        {
            "time": ENTRY_AT + timedelta(minutes=1),
            "open": 100.0,
            "high": 102.2,
            "low": 100.5,
            "close": 102.0,
        },
        {
            "time": ENTRY_AT + timedelta(minutes=2),
            "open": 102.0,
            "high": 102.0,
            "low": 101.0,
            "close": 101.2,
        },
    ]
    monkeypatch.setattr(
        shadow_trade_monitor.shadow_trade_service,
        "_get_market_metadata_price",
        AsyncMock(return_value=(None, None)),
    )
    monkeypatch.setattr(
        shadow_trade_monitor.shadow_trade_service,
        "_get_current_price_multi_tf",
        AsyncMock(return_value=(None, None)),
    )
    monkeypatch.setattr(
        shadow_trade_monitor.shadow_trade_service,
        "_get_current_ohlc_multi_tf",
        AsyncMock(return_value=(None, None, None)),
    )
    monkeypatch.setattr(
        shadow_trade_monitor, "_fetch_candles", AsyncMock(return_value=candles)
    )

    transition = await _advance_shadow(object(), shadow)

    assert transition == "completed"
    assert shadow.outcome == "TRAILING_STOP"
    assert shadow.exit_price == pytest.approx(101.178)
    assert shadow.barrier_touched == "TRAILING"
    assert shadow.eligible_for_training is False


@pytest.mark.asyncio
async def test_same_candle_activation_and_retrace_is_not_invented(monkeypatch):
    shadow = _shadow(_trailing_snapshot())
    candle = {
        "time": ENTRY_AT + timedelta(minutes=1),
        "open": 100.0,
        "high": 102.2,
        "low": 101.0,
        "close": 101.2,
    }
    monkeypatch.setattr(
        shadow_trade_monitor.shadow_trade_service,
        "_get_market_metadata_price",
        AsyncMock(return_value=(None, None)),
    )
    monkeypatch.setattr(
        shadow_trade_monitor.shadow_trade_service,
        "_get_current_price_multi_tf",
        AsyncMock(return_value=(None, None)),
    )
    monkeypatch.setattr(
        shadow_trade_monitor.shadow_trade_service,
        "_get_current_ohlc_multi_tf",
        AsyncMock(return_value=(None, None, None)),
    )
    monkeypatch.setattr(
        shadow_trade_monitor, "_fetch_candles", AsyncMock(return_value=[candle])
    )

    transition = await _advance_shadow(object(), shadow)

    assert transition == "running"
    assert shadow.outcome is None
    assert shadow.max_price_post_entry == pytest.approx(102.2)


@pytest.mark.asyncio
async def test_armed_trailing_preempts_lower_static_sl(monkeypatch):
    shadow = _shadow(_trailing_snapshot())
    shadow.max_price_post_entry = 102.2
    candle = {
        "time": ENTRY_AT + timedelta(minutes=2),
        "open": 101.5,
        "high": 101.8,
        "low": 89.0,
        "close": 90.0,
    }
    monkeypatch.setattr(
        shadow_trade_monitor.shadow_trade_service,
        "_get_market_metadata_price",
        AsyncMock(return_value=(None, None)),
    )
    monkeypatch.setattr(
        shadow_trade_monitor.shadow_trade_service,
        "_get_current_price_multi_tf",
        AsyncMock(return_value=(None, None)),
    )
    monkeypatch.setattr(
        shadow_trade_monitor.shadow_trade_service,
        "_get_current_ohlc_multi_tf",
        AsyncMock(return_value=(None, None, None)),
    )
    monkeypatch.setattr(
        shadow_trade_monitor, "_fetch_candles", AsyncMock(return_value=[candle])
    )

    transition = await _advance_shadow(object(), shadow)

    assert transition == "completed"
    assert shadow.outcome == "TRAILING_STOP"
    assert shadow.exit_price == pytest.approx(101.178)


@pytest.mark.asyncio
async def test_trailing_exit_is_not_written_as_fixed_barrier_ml_label():
    shadow = _shadow(_trailing_snapshot())
    shadow.outcome = "TRAILING_STOP"
    db = AsyncMock()

    simulation_id = await record_as_simulation(db, shadow)

    assert simulation_id is None
    db.execute.assert_not_awaited()


class _AtrResult:
    def fetchone(self):
        return SimpleNamespace(atr_pct="0.6056", time=ENTRY_AT)


class _AtrDb:
    def __init__(self):
        self.statement = None
        self.params = None

    async def execute(self, statement, params):
        self.statement = str(statement)
        self.params = params
        return _AtrResult()


@pytest.mark.asyncio
async def test_point_in_time_atr_uses_configured_timeframe_and_cutoff():
    db = _AtrDb()

    atr_pct, source_at = await _load_point_in_time_atr_pct(
        db,
        symbol="INJ_USDT",
        timeframe="5m",
        as_of=ENTRY_AT,
    )

    assert atr_pct == pytest.approx(0.6056)
    assert source_at == ENTRY_AT
    assert db.params["timeframe"] == "5m"
    assert db.params["as_of"] == ENTRY_AT
    assert "time <= :as_of" in db.statement
