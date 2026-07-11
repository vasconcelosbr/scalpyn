from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4
from unittest.mock import AsyncMock, patch

import pytest

from app.api.shadow_trades import _to_read
from app.services.shadow_trade_service import _create_from_decision, _merge_ml_shadow_config


def test_shadow_trade_list_item_exposes_profile_attribution():
    profile_id = uuid4()
    row = SimpleNamespace(
        id=uuid4(),
        symbol="BTC_USDT",
        direction="SPOT",
        entry_price=100.0,
        tp_price=102.0,
        sl_price=99.0,
        amount_usdt=25.0,
        outcome=None,
        pnl_pct=None,
        pnl_usdt=None,
        status="RUNNING",
        skip_reason=None,
        holding_seconds=60,
        created_at=None,
        completed_at=None,
        entry_timestamp=None,
        profile_id=profile_id,
        profile_name="generated-profile",
        btc_price_at_entry=None,
        btc_change_1h_pct=None,
        funding_rate_at_entry=None,
        n_concurrent_signals=None,
        mae_pct=None,
        mfe_pct=None,
        max_drawdown_pct=None,
        max_profit_pct=None,
    )

    item = _to_read(row, current_price=101.0)

    assert item.profile_id == profile_id
    assert item.profile_name == "generated-profile"


@pytest.mark.asyncio
async def test_create_from_decision_copies_profile_attribution():
    profile_id = uuid4()
    profile_version = datetime(2026, 6, 19, tzinfo=timezone.utc)
    entry_time = datetime(2026, 6, 19, 1, 0, tzinfo=timezone.utc)
    decision = SimpleNamespace(
        id=123,
        user_id=uuid4(),
        symbol="BTC_USDT",
        strategy="profile-signal",
        direction="SPOT",
        created_at=entry_time,
        metrics={},
        profile_id=profile_id,
        profile_version=profile_version,
        profile_name="generated-profile",
    )

    result = SimpleNamespace(fetchone=lambda: (uuid4(),))
    db = AsyncMock()
    db.execute.return_value = result

    with (
        patch(
            "app.services.shadow_trade_service._get_current_price_multi_tf",
            new=AsyncMock(return_value=(100.0, entry_time)),
        ),
        patch(
            "app.services.shadow_trade_service._build_features_snapshot",
            return_value={},
        ),
    ):
        created_id = await _create_from_decision(
            db,
            decision,
            "NOT_TRADABLE",
            {"tp_pct": 2.0, "sl_pct": 1.0},
        )

    assert created_id is not None
    params = db.execute.await_args.args[1]
    assert params["profile_id"] == profile_id
    assert params["profile_version"] == profile_version
    assert params["profile_name"] == "generated-profile"
    assert params["strategy_type"] == "PROFILE_L3"


@pytest.mark.asyncio
async def test_create_from_legacy_decision_keeps_profile_fields_null():
    entry_time = datetime(2026, 6, 19, 1, 0, tzinfo=timezone.utc)
    decision = SimpleNamespace(
        id=124,
        user_id=uuid4(),
        symbol="ETH_USDT",
        strategy="global-signal",
        direction="SPOT",
        created_at=entry_time,
        metrics={},
    )

    result = SimpleNamespace(fetchone=lambda: (uuid4(),))
    db = AsyncMock()
    db.execute.return_value = result

    with (
        patch(
            "app.services.shadow_trade_service._get_current_price_multi_tf",
            new=AsyncMock(return_value=(100.0, entry_time)),
        ),
        patch(
            "app.services.shadow_trade_service._build_features_snapshot",
            return_value={},
        ),
    ):
        await _create_from_decision(
            db,
            decision,
            "NOT_TRADABLE",
            {"tp_pct": 2.0, "sl_pct": 1.0},
        )

    params = db.execute.await_args.args[1]
    assert params["profile_id"] is None
    assert params["profile_version"] is None
    assert params["profile_name"] is None
    assert params["strategy_type"] is None


@pytest.mark.asyncio
async def test_ml_shadow_tp_override_applies_in_fixed_barrier_mode():
    """The ML economic contract must not drift with spot-engine TP changes."""
    entry_time = datetime(2026, 7, 8, tzinfo=timezone.utc)
    decision = SimpleNamespace(
        id=125,
        user_id=uuid4(),
        symbol="SOL_USDT",
        strategy="profile-signal",
        direction="SPOT",
        created_at=entry_time,
        metrics={},
    )
    db = AsyncMock()
    db.execute.return_value = SimpleNamespace(fetchone=lambda: (uuid4(),))

    with (
        patch(
            "app.services.shadow_trade_service._get_current_price_multi_tf",
            new=AsyncMock(return_value=(100.0, entry_time)),
        ),
        patch(
            "app.services.shadow_trade_service._build_features_snapshot",
            return_value={},
        ),
    ):
        await _create_from_decision(
            db,
            decision,
            "NOT_TRADABLE",
            {
                "tp_pct": 0.6,
                "sl_pct": 1.0,
                "shadow_barrier_mode": "FIXED",
                "shadow_tp_pct": 1.5,
            },
        )

    params = db.execute.await_args.args[1]
    assert params["barrier_mode"] == "FIXED"
    assert params["tp_pct"] == 1.5
    assert params["tp_pct_applied"] == 1.5
    assert params["tp_price"] == pytest.approx(101.5)


def test_merge_ml_shadow_config_carries_full_economic_contract():
    merged = _merge_ml_shadow_config(
        {"tp_pct": 0.6, "sl_pct": 1.0},
        {
            "shadow_tp_pct": 1.5,
            "shadow_barrier_mode": "ATR_DYNAMIC",
            "shadow_atr_multiplier_sl": 1.5,
            "shadow_barrier_min_pct": 0.5,
            "shadow_barrier_max_pct": 3.0,
            "ml_fee_roundtrip_pct": 0.2,
        },
    )

    assert merged["tp_pct"] == 0.6
    assert merged["shadow_tp_pct"] == 1.5
    assert merged["shadow_barrier_mode"] == "ATR_DYNAMIC"
    assert merged["sl_atr_multiplier"] == 1.5
    assert merged["ml_fee_roundtrip_pct"] == 0.2
