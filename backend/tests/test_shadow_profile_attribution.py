from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4
from unittest.mock import AsyncMock, patch

import pytest

from app.services.shadow_trade_service import _create_from_decision


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
