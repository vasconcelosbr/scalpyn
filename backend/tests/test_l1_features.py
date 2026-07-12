"""
B1.3 — Unit tests for L1_SPECTRUM features capture.

Tests:
  1. Symbol with full indicators → snapshot has 37 indicator keys + 3 metadata keys,
     coverage = 1.0, _oldest_indicator_age_s is an integer.
  2. Symbol with NO indicators in the provider → shadow still created,
     snapshot contains only metadata keys, coverage = 0.0.
  3. get_merged_indicators raising an exception → shadow still created with empty features.

Pureza invariant: in all three cases, shadow creation is NOT skipped.
"""

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _make_merged_indicators(indicators: dict) -> MagicMock:
    """Return a MagicMock that looks like a MergedIndicators instance."""
    mi = MagicMock()
    ts_fixed = datetime(2026, 6, 11, 12, 0, 0, tzinfo=timezone.utc)
    mi.as_flat_dict.return_value = indicators
    mi.meta = {
        k: {"timestamp": ts_fixed, "group": "test", "stale": False}
        for k in indicators
    }
    return mi


def _make_db_session_mock():
    """Minimal async context-manager mock for CeleryAsyncSessionLocal."""
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.begin = MagicMock(return_value=AsyncMock(
        __aenter__=AsyncMock(return_value=None),
        __aexit__=AsyncMock(return_value=False),
    ))
    return session


# ─── Feature count used in production ─────────────────────────────────────────

EXPECTED_N_FEATURES = 37

_FULL_INDICATORS = {f"ind_{i}": float(i) for i in range(EXPECTED_N_FEATURES)}


# ─── Tests ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_l1_shadow_full_coverage():
    """Symbol with all 37 indicators → coverage=1.0 and all metadata keys present."""
    from backend.app.services.shadow_trade_service import create_l1_spectrum_shadows

    merged_result = {"BTC_USDT": _make_merged_indicators(_FULL_INDICATORS)}

    _created_metrics: list = []

    async def _fake_create_from_decision(
        db, decision, skip_reason, user_config, *, source=None, lineage=None
    ):
        _created_metrics.append(decision.metrics)
        return "fake-uuid-1"

    db_mock = _make_db_session_mock()
    # _skp_row for ml config (ml_fee + shadow_capture_l1_enabled etc.)
    ml_cfg = MagicMock()
    ml_cfg.config_json = {
        "shadow_capture_l1_enabled": True,
        "shadow_capture_l1_sample_rate": 1.0,  # 100% → BTC_USDT always sampled
        "shadow_capture_l1_max_per_hour": 200,
        "ml_fee_roundtrip_pct": 0.20,
    }
    se_cfg = MagicMock()
    se_cfg.config_json = {}

    ml_res = MagicMock()
    ml_res.scalar_one_or_none.return_value = ml_cfg
    se_res = MagicMock()
    se_res.scalar_one_or_none.return_value = se_cfg

    db_mock.execute = AsyncMock(side_effect=[
        ml_res, se_res,     # config load
        MagicMock(scalar_one=MagicMock(return_value=0)),  # rate limit count
    ])

    with (
        patch(
            "backend.app.database.CeleryAsyncSessionLocal",
            return_value=db_mock,
        ),
        patch(
            "backend.app.services.indicators_provider.get_merged_indicators",
            new=AsyncMock(return_value=merged_result),
        ),
        patch(
            "backend.app.services.shadow_trade_service._create_from_decision",
            new=AsyncMock(side_effect=_fake_create_from_decision),
        ),
        patch(
            "backend.app.schemas.spot_engine_config.SpotEngineConfig",
        ),
    ):
        created = await create_l1_spectrum_shadows(
            user_id="user-1",
            symbols=["BTC_USDT"],
            execution_id="exec-001",
            assets_by_symbol={"BTC_USDT": {"current_price": 65000.0}},
            promotion_at=datetime(2026, 6, 11, 14, 0, 0, tzinfo=timezone.utc),
        )

    assert created == 1
    assert len(_created_metrics) == 1

    snap = _created_metrics[0]["indicators_snapshot"]
    assert "_features_coverage" in snap
    assert "_features_captured_at" in snap
    assert "_oldest_indicator_age_s" in snap

    coverage = snap["_features_coverage"]["value"]
    assert coverage == pytest.approx(1.0, abs=0.01), f"expected 1.0 got {coverage}"

    age = snap["_oldest_indicator_age_s"]["value"]
    assert isinstance(age, int), f"expected int got {type(age)}"

    # All 37 indicator keys present
    indicator_keys = [k for k in snap if not k.startswith("_")]
    assert len(indicator_keys) == EXPECTED_N_FEATURES


@pytest.mark.asyncio
async def test_l1_shadow_no_indicators():
    """Symbol not found in indicator provider → shadow still created; coverage=0.0."""
    from backend.app.services.shadow_trade_service import create_l1_spectrum_shadows

    # Provider returns empty dict — symbol not found
    merged_result: dict = {}

    _created_metrics: list = []

    async def _fake_create_from_decision(
        db, decision, skip_reason, user_config, *, source=None, lineage=None
    ):
        _created_metrics.append(decision.metrics)
        return "fake-uuid-2"

    db_mock = _make_db_session_mock()
    ml_cfg = MagicMock()
    ml_cfg.config_json = {
        "shadow_capture_l1_enabled": True,
        "shadow_capture_l1_sample_rate": 1.0,
        "shadow_capture_l1_max_per_hour": 200,
        "ml_fee_roundtrip_pct": 0.20,
    }
    se_cfg = MagicMock()
    se_cfg.config_json = {}

    ml_res = MagicMock()
    ml_res.scalar_one_or_none.return_value = ml_cfg
    se_res = MagicMock()
    se_res.scalar_one_or_none.return_value = se_cfg

    db_mock.execute = AsyncMock(side_effect=[
        ml_res, se_res,
        MagicMock(scalar_one=MagicMock(return_value=0)),
    ])

    with (
        patch(
            "backend.app.database.CeleryAsyncSessionLocal",
            return_value=db_mock,
        ),
        patch(
            "backend.app.services.indicators_provider.get_merged_indicators",
            new=AsyncMock(return_value=merged_result),
        ),
        patch(
            "backend.app.services.shadow_trade_service._create_from_decision",
            new=AsyncMock(side_effect=_fake_create_from_decision),
        ),
        patch(
            "backend.app.schemas.spot_engine_config.SpotEngineConfig",
        ),
    ):
        created = await create_l1_spectrum_shadows(
            user_id="user-1",
            symbols=["ETH_USDT"],
            execution_id="exec-002",
            assets_by_symbol={"ETH_USDT": {"current_price": 3500.0}},
            promotion_at=datetime(2026, 6, 11, 14, 5, 0, tzinfo=timezone.utc),
        )

    # Shadow still created despite no indicators (pureza invariant)
    assert created == 1
    assert len(_created_metrics) == 1

    snap = _created_metrics[0]["indicators_snapshot"]
    assert "_features_coverage" in snap
    assert snap["_features_coverage"]["value"] == 0.0

    # No real indicator keys
    indicator_keys = [k for k in snap if not k.startswith("_")]
    assert indicator_keys == []


@pytest.mark.asyncio
async def test_l1_shadow_provider_exception():
    """get_merged_indicators raises → shadow still created with empty features."""
    from backend.app.services.shadow_trade_service import create_l1_spectrum_shadows

    _created_metrics: list = []

    async def _fake_create_from_decision(
        db, decision, skip_reason, user_config, *, source=None, lineage=None
    ):
        _created_metrics.append(decision.metrics)
        return "fake-uuid-3"

    db_mock = _make_db_session_mock()
    ml_cfg = MagicMock()
    ml_cfg.config_json = {
        "shadow_capture_l1_enabled": True,
        "shadow_capture_l1_sample_rate": 1.0,
        "shadow_capture_l1_max_per_hour": 200,
        "ml_fee_roundtrip_pct": 0.20,
    }
    se_cfg = MagicMock()
    se_cfg.config_json = {}

    ml_res = MagicMock()
    ml_res.scalar_one_or_none.return_value = ml_cfg
    se_res = MagicMock()
    se_res.scalar_one_or_none.return_value = se_cfg

    db_mock.execute = AsyncMock(side_effect=[
        ml_res, se_res,
        MagicMock(scalar_one=MagicMock(return_value=0)),
    ])

    async def _exploding_get_merged(*args, **kwargs):
        raise RuntimeError("DB connection refused")

    with (
        patch(
            "backend.app.database.CeleryAsyncSessionLocal",
            return_value=db_mock,
        ),
        patch(
            "backend.app.services.indicators_provider.get_merged_indicators",
            new=_exploding_get_merged,
        ),
        patch(
            "backend.app.services.shadow_trade_service._create_from_decision",
            new=AsyncMock(side_effect=_fake_create_from_decision),
        ),
        patch(
            "backend.app.schemas.spot_engine_config.SpotEngineConfig",
        ),
    ):
        created = await create_l1_spectrum_shadows(
            user_id="user-1",
            symbols=["SOL_USDT"],
            execution_id="exec-003",
            assets_by_symbol={"SOL_USDT": {"current_price": 170.0}},
            promotion_at=datetime(2026, 6, 11, 14, 10, 0, tzinfo=timezone.utc),
        )

    # Shadow created even though provider exploded (pureza + fire-and-forget)
    assert created == 1
