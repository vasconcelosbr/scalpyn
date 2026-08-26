import inspect
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_db_session_mock():
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.begin = MagicMock(
        return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=None),
            __aexit__=AsyncMock(return_value=False),
        )
    )
    return session


@pytest.mark.asyncio
async def test_strategy_lab_merged_features_loader_preserves_source_metadata():
    from backend.app.services.shadow_trade_service import (
        _load_strategy_lab_features_by_symbol,
    )

    merged_item = MagicMock()
    merged_item.as_flat_dict.return_value = {
        "atr_pct": 1.23,
        "volume_24h_base": 456.0,
        "psar_trend": "RISING",
        "last_candle_time": datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc),
    }
    merged_item.meta = {
        "atr_pct": {"timestamp": "2026-08-01T12:00:00+00:00"}
    }
    db_mock = _make_db_session_mock()

    with (
        patch("backend.app.database.CeleryAsyncSessionLocal", return_value=db_mock),
        patch(
            "backend.app.services.indicators_provider.get_merged_indicators",
            new=AsyncMock(return_value={"BTC_USDT": merged_item}),
        ),
    ):
        features = await _load_strategy_lab_features_by_symbol(
            ["BTC_USDT", "BTC_USDT"]
        )

    assert features == {
        "BTC_USDT": {
            "features": {
                "atr_pct": 1.23,
                "volume_24h_base": 456.0,
                "psar_trend": "RISING",
            },
            "metadata": merged_item.meta,
        }
    }


def test_shadow_feature_snapshot_drops_temporal_metadata_values():
    from types import SimpleNamespace

    from backend.app.services.shadow_trade_service import _build_features_snapshot

    decision = SimpleNamespace(
        metrics={
            "indicators_snapshot": {
                "atr_pct": {"value": 1.23},
                "last_candle_time": {
                    "value": datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
                },
            }
        }
    )

    assert _build_features_snapshot(decision) == {"atr_pct": 1.23}


def test_strategy_lab_shadow_paths_use_canonical_feature_loader():
    from backend.app.services.shadow_trade_service import (
        create_strategy_lab_rejected_shadows,
        create_strategy_lab_shadows,
    )

    allow_src = inspect.getsource(create_strategy_lab_shadows)
    rejected_src = inspect.getsource(create_strategy_lab_rejected_shadows)

    assert "_load_strategy_lab_features_by_symbol" in allow_src
    assert "_load_strategy_lab_features_by_symbol" in rejected_src


def test_l3_simulated_uses_canonical_features_and_profile_lineage():
    from backend.app.services.shadow_trade_service import create_l3_simulated_shadows

    source = inspect.getsource(create_l3_simulated_shadows)

    assert "_load_strategy_lab_features_by_symbol" in source
    assert 'metrics["indicators_snapshot"] = dict(canonical_features)' in source
    assert "profile_id=str(profile_id) if profile_id else None" in source


def test_l3_rejected_uses_canonical_features():
    from backend.app.services.shadow_trade_service import create_l3_rejected_inline_shadows

    source = inspect.getsource(create_l3_rejected_inline_shadows)

    assert "_load_strategy_lab_features_by_symbol" in source
    assert 'metrics["indicators_snapshot"] = dict(canonical_features)' in source
