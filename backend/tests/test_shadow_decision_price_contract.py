from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.services.shadow_trade_service import _create_from_decision


@pytest.mark.asyncio
async def test_new_shadow_uses_frozen_decision_price_without_post_decision_lookup() -> None:
    decision_at = datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc)
    source_at = decision_at - timedelta(seconds=2)
    decision = SimpleNamespace(
        id=123,
        user_id=uuid4(),
        symbol="BTC_USDT",
        strategy="profile-signal",
        direction="SPOT",
        created_at=decision_at,
        metrics={
            "price_envelope": {
                "value": 101.25,
                "source": "market_metadata",
                "source_at": source_at.isoformat(),
            },
            "indicators_snapshot": {},
        },
    )
    result = SimpleNamespace(fetchone=lambda: (uuid4(),))
    db = AsyncMock()
    db.execute.return_value = result
    db.begin_nested = MagicMock()
    runtime_config = {
        "tp_pct": 2.0,
        "sl_pct": 1.0,
        "amount_usdt": 100.0,
        "timeout_candles": 60,
        "ttt_enabled": False,
        "ttt_tp_pct": 1.0,
        "ttt_timeout_minutes": 180,
        "trailing": {"enabled": False},
        "ml_fee_roundtrip_pct": 0.2,
        "shadow_entry_max_lag_seconds": 5,
        "shadow_measurement_timeframe_priority": ["1m", "5m"],
    }

    with (
        patch(
            "app.services.shadow_trade_service._get_current_price_multi_tf",
            new=AsyncMock(side_effect=AssertionError("post-decision lookup used")),
        ),
        patch("app.services.shadow_trade_service._build_features_snapshot", return_value={}),
    ):
        created = await _create_from_decision(
            db, decision, "NOT_TRADABLE", runtime_config
        )

    assert created is not None
    insert_values = db.execute.call_args.args[1]
    assert insert_values["entry_price"] == 101.25
    assert insert_values["entry_timestamp"] == decision_at
    config_snapshot = __import__("json").loads(insert_values["config_snapshot"])
    assert config_snapshot["entry_price_reference"] == 101.25
    assert config_snapshot["entry_price_realized"] is None
    assert config_snapshot["entry_price_lag_seconds"] == 2.0
    assert config_snapshot["entry_quality"] == "OK"
