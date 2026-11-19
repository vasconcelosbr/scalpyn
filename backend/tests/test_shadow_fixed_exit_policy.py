from copy import deepcopy
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from app.schemas.strategy_settings import MLShadowConfig
from app.services.shadow_trade_service import (
    _apply_barrier_params,
    _resolve_lab_barrier,
)
from app.tasks import shadow_trade_monitor as monitor
from test_shadow_trailing_contract import _shadow, ENTRY_AT


def fixed_config():
    return MLShadowConfig(
        shadow_barrier_mode="FIXED",
        ml_active_barrier_contract_version="shadow_fixed_v1",
        shadow_fixed_tp_pct=1.0,
        shadow_fixed_sl_pct=5.0,
        shadow_only_tp_sl=True,
    ).model_dump()


def test_fixed_shadow_exit_overrides_are_independent_and_lab_matches():
    original = {"tp_pct": 2.0, "sl_pct": 3.0, "timeout_candles": 10,
                "trailing": {"enabled": True, "activation_profit_pct": 0.5}}
    runtime = _apply_barrier_params(deepcopy(original), fixed_config())
    assert (runtime["tp_pct"], runtime["sl_pct"], runtime["timeout_candles"]) == (1, 5, 0)
    assert runtime["trailing"]["enabled"] is False
    assert original["trailing"]["enabled"] is True
    lab = _apply_barrier_params({}, fixed_config())
    assert _resolve_lab_barrier({}, 2, 3, lab, symbol="TEST", log_tag="test") == (
        "FIXED", 1, 5, "shadow_fixed_v1")


def test_legacy_and_atr_keep_existing_behavior():
    base = {"tp_pct": 2, "sl_pct": 3, "timeout_candles": 10, "trailing": {"enabled": True}}
    for ml in ({"shadow_barrier_mode": "FIXED"}, {
        "shadow_barrier_mode": "ATR_DYNAMIC", "shadow_fixed_tp_pct": 1, "shadow_fixed_sl_pct": 5
    }):
        result = _apply_barrier_params(deepcopy(base), ml)
        assert all(result[k] == v for k, v in base.items())
        assert result["shadow_only_tp_sl"] is False


@pytest.mark.parametrize("patch", [
    {"shadow_fixed_sl_pct": None}, {"shadow_fixed_sl_pct": -5},
    {"shadow_fixed_sl_pct": 100}, {"shadow_fixed_tp_pct": 0},
    {"shadow_barrier_mode": "ATR_DYNAMIC", "ml_active_barrier_contract_version": "shadow_atr_dynamic_v2"},
])
def test_fixed_policy_rejects_incomplete_or_invalid_settings(patch):
    with pytest.raises(ValidationError):
        MLShadowConfig.model_validate({**fixed_config(), **patch})


@pytest.mark.asyncio
@pytest.mark.parametrize("low,high,outcome,exit_price", [
    (95.01, 100.99, None, None),
    (95.0, 100.99, "SL_HIT", 95.0),
    (95.01, 101.0, "TP_HIT", 101.0),
    (95.0, 101.0, "SL_HIT", 95.0),
])
async def test_fixed_boundaries_hold_or_close_without_early_exit(monkeypatch, low, high, outcome, exit_price):
    runtime = _apply_barrier_params({"trailing": {"enabled": True}}, fixed_config())
    shadow = _shadow({"shadow_only_tp_sl": True, "trailing": runtime["trailing"],
                      "shadow_l3_exit_policy": {"config": {"mode": "APPLY"}}})
    shadow.source = "L3_CONSOLIDATED"
    shadow.tp_price, shadow.sl_price = 101.0, 95.0
    shadow.tp_pct, shadow.sl_pct = 1.0, 5.0
    shadow.barrier_mode = "FIXED"
    shadow.timeout_candles = runtime["timeout_candles"]
    for name, value in (("_get_market_metadata_price", (None, None)),
                        ("_get_current_price_multi_tf", (None, None)),
                        ("_get_current_ohlc_multi_tf", (None, None, None))):
        monkeypatch.setattr(monitor.shadow_trade_service, name, AsyncMock(return_value=value))
    monkeypatch.setattr(monitor, "_fetch_candles", AsyncMock(return_value=[{
        "time": ENTRY_AT + timedelta(days=10), "open": 100.0,
        "high": high, "low": low, "close": 99.0,
    }]))
    result = await monitor._advance_shadow(object(), shadow, {
        "enabled": True, "max_age_hours": 1, "policy": "TIMEOUT_LAST_KNOWN_PRICE"})
    assert shadow.outcome == outcome
    assert result == ("completed" if outcome else "running")
    if outcome:
        assert shadow.exit_price == pytest.approx(exit_price)


@pytest.mark.asyncio
async def test_managed_exit_cannot_override_frozen_fixed_policy():
    from app.services.shadow_l3_exit_service import advance_shadow
    shadow = _shadow({"shadow_only_tp_sl": True,
                      "shadow_l3_exit_policy": {"config": {"mode": "APPLY"}}})
    shadow.source = "L3"
    assert await advance_shadow(object(), shadow) is None
    assert shadow.outcome is None


@pytest.mark.asyncio
async def test_creation_freezes_fixed_snapshot_and_disables_early_exits(monkeypatch):
    import json
    from types import SimpleNamespace
    from unittest.mock import MagicMock
    from uuid import uuid4
    from app.services.shadow_trade_service import _create_from_decision
    from app.schemas.watchlist_lineage_context import WatchlistLineageContext
    from test_shadow_barrier_v2_fail_closed import _base_user_config, _decision, admit_barrier_test
    admit_barrier_test.__wrapped__(monkeypatch)
    result = SimpleNamespace(fetchone=lambda: (uuid4(),), fetchall=lambda: [], first=lambda: None,
        scalar_one_or_none=lambda: None, mappings=lambda: SimpleNamespace(first=lambda: None))
    db = AsyncMock()
    db.execute.return_value = result
    db.begin_nested = MagicMock()
    cfg = _apply_barrier_params(_base_user_config(), fixed_config())
    monkeypatch.setattr("app.services.shadow_trade_service._get_current_price_multi_tf", AsyncMock(return_value=(100.0, ENTRY_AT)))
    created = await _create_from_decision(db, _decision(), "NOT_TRADABLE", cfg,
        lineage=WatchlistLineageContext(watchlist_id=str(uuid4()), watchlist_name="test", watchlist_level="L3",
        profile_id=str(uuid4()), profile_name="test", profile_version=ENTRY_AT,
        rules_snapshot={"entry_triggers": {"conditions": []}}))
    assert created is not None
    params = next(c.args[1] for c in db.execute.await_args_list if len(c.args)>1 and "barrier_contract_version" in c.args[1])
    assert (params["tp_price"],params["sl_price"],params["timeout_candles"]) == (101.0,95.0,0)
    snap = params["config_snapshot"]
    if isinstance(snap,str): snap=json.loads(snap)
    assert snap["shadow_only_tp_sl"] is True
    assert snap["trailing"]["enabled"] is False
    assert "shadow_l3_exit_policy" not in snap
