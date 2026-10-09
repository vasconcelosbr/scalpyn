from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.services.shadow_trade_service import _create_from_decision
from app.schemas.watchlist_lineage_context import WatchlistLineageContext
from app.services.shadow_entry_quote import EntryQuoteUnavailable


@pytest.mark.asyncio
@pytest.mark.parametrize("quote_failure", [None, "ENTRY_QUOTE_STALE", "ENTRY_QUOTE_INSUFFICIENT_DEPTH"])
async def test_new_shadow_uses_gate_quote_and_does_not_backdate_the_fill(quote_failure) -> None:
    decision_at = datetime.now(timezone.utc) - timedelta(seconds=15)
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

    lineage = WatchlistLineageContext(
        watchlist_id=str(uuid4()), watchlist_name="Test L3", watchlist_level="L3",
        profile_id=str(uuid4()), profile_name="Test", profile_version=decision_at,
        rules_snapshot={"scoring": {}}, profile_version_id=str(uuid4()),
        score_engine_version_id=str(uuid4()),
    )
    captured_at = decision_at + timedelta(seconds=15)
    quote = {"value": 102.0, "source_at": captured_at.isoformat(),
             "entry_at": captured_at.isoformat(), "age_seconds": 0.0,
             "contract_version": "gate_spot_entry_quote_v1"}
    with (
        patch("app.services.shadow_entry_quote.capture_entry_quote", new=AsyncMock(return_value=quote, side_effect=EntryQuoteUnavailable(quote_failure) if quote_failure else None)),
        patch("app.services.shadow_trade_service._has_active_profile_shadow", new=AsyncMock(return_value=False)),
        patch("app.services.shadow_l3_exit_service.load_frozen_policy", new=AsyncMock(return_value={})),
        patch("app.services.shadow_l3_exit_service.register_shadow", new=AsyncMock()),
        patch("app.services.pool_service.radar_shadow_entry_is_eligible", new=AsyncMock(return_value=True)),
        patch(
            "app.services.shadow_trade_service._get_current_price_multi_tf",
            new=AsyncMock(side_effect=AssertionError("post-decision lookup used")),
        ),
        patch("app.services.shadow_trade_service._build_features_snapshot", return_value={}),
    ):
        if quote_failure:
            with pytest.raises(EntryQuoteUnavailable, match=quote_failure):
                await _create_from_decision(db, decision, "NOT_TRADABLE", runtime_config, lineage=lineage)
            db.execute.assert_not_called()
            return
        created = await _create_from_decision(
            db, decision, "NOT_TRADABLE", runtime_config, lineage=lineage
        )

    assert created is not None
    insert_values = db.execute.call_args.args[1]
    assert insert_values["entry_price"] == 102.0
    assert insert_values["entry_timestamp"] == captured_at
    config_snapshot = __import__("json").loads(insert_values["config_snapshot"])
    assert config_snapshot["entry_price_reference"] == 101.25
    assert config_snapshot["entry_price_realized"] is None
    assert config_snapshot["entry_price_lag_seconds"] == 0.0
    assert config_snapshot["entry_price_observed"] == 102.0
    assert config_snapshot["entry_quote"] == quote
    assert config_snapshot["entry_decision_id"] == decision.id
    assert config_snapshot["entry_quality"] == "OK"
