import inspect
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
async def test_strategy_lab_merged_features_loader_uses_canonical_flat_dict():
    from backend.app.services.shadow_trade_service import (
        _load_strategy_lab_features_by_symbol,
    )

    merged_item = MagicMock()
    merged_item.as_flat_dict.return_value = {
        "atr_pct": 1.23,
        "volume_24h_base": 456.0,
        "psar_trend": "RISING",
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
            "atr_pct": 1.23,
            "volume_24h_base": 456.0,
            "psar_trend": "RISING",
        }
    }


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
