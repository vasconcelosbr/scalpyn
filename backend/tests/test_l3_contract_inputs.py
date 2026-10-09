from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.l3_contract_inputs import closed_di_candidates, prepare_l3_contract_inputs
from app.services.l3_authorization_contract_v3 import build_authorization_contract
from app.services.l3_gate_compiler_v2 import evaluate_l3_gate_v2

NOW = datetime(2026, 10, 9, 15, tzinfo=timezone.utc)
CONFIG = {"adx": {"enabled": True, "period": 14}}


def candles():
    return [{"time": NOW - timedelta(minutes=40 - i), "high": 100 + i,
             "low": 98 + i, "close": 99 + i, "exchange": "gate.io", "is_closed": True,
             "ingested_at": NOW - timedelta(minutes=39 - i), "capture_contract_version": "closed-v1"}
            for i in range(40)]


def profile():
    return {"default_timeframe": "5m", "filters": {"conditions": [
        {"id": "di", "indicator": "di_trend", "operator": "==", "value": True,
         "required": False, "source": "ohlcv", "source_provider": "gate.io",
         "provider_policy_id": "spot_gate_closed_ohlcv_v1", "timeframe": "1m",
         "candle_policy": "CLOSED_ONLY", "max_age_seconds": 741}]},
        "signals": {"conditions": [{"id": "spread", "indicator": "spread_pct", "operator": "<=",
            "value": 0.2, "required": False, "source": "live_order_book", "source_provider": "gate",
            "provider_policy_id": "observed_order_book_v1", "snapshot": True, "max_age_seconds": 600}]},
        "entry_triggers": {"conditions": [{"id": "taker", "indicator": "taker_ratio", "operator": ">=",
            "value": 0.5, "required": True, "source": "live_trade_flow", "source_provider": "gate_trades_ws_spot",
            "provider_policy_id": "l3_live_trade_flow_v1", "window_seconds": 300, "max_age_seconds": 60}]},
        "block_rules": {"blocks": []}}


def policy():
    return {"profile_id": "p", "l3_v3_provenance_resolver": {
        "enabled": True, "profile_allowlist": ["p"], "policy_version": "l3_v3_provenance_resolver_v1",
        "source_policies": {source: {"allowed_source_providers": [provider], "scheduler_group": "microstructure"}
                            for source, provider in [("ohlcv", "gate.io"), ("live_order_book", "gate"),
                                                     ("live_trade_flow", "gate_trades_ws_spot")]}}}


def asset():
    return {"symbol": "TEST_USDT", "indicators": {"di_trend": False, "spread_pct": 0.7, "taker_ratio": 0.6},
            "_merged_indicators": SimpleNamespace(candidates=[]),
            "_l3_live_order_flow_snapshot": {"values": {"taker_ratio": 0.6}, "meta": {
                "source_provider": "gate_trades_ws_spot", "window_seconds": 300,
                "source_timestamp": NOW.isoformat()}}}


def authorize(a, p):
    p = {**p, "_l3_gate_runtime_policy": policy()}
    gate = evaluate_l3_gate_v2(asset=a, profile_config=p, score=80, score_context={},
                               evaluated_at=NOW, base_eligible=True, legacy_decision="ALLOW")
    return build_authorization_contract(asset=a, profile_config=p, legacy_decision="ALLOW",
        evaluated_at=NOW, profile_id="p", profile_name="PUMP3", profile_version=NOW,
        gate_evaluation=gate, runtime_policy=policy())


@pytest.mark.parametrize("mutation", ["gap", "open", "future", "provider", "nan", "capture"])
def test_di_never_fabricates_identity_from_invalid_candles(mutation):
    rows = candles()
    if mutation == "gap": rows[5]["time"] -= timedelta(minutes=1)
    if mutation == "open": rows[-1]["is_closed"] = False
    if mutation == "future": rows[-1]["ingested_at"] = NOW + timedelta(seconds=1)
    if mutation == "provider": rows[-1]["exchange"] = "binance"
    if mutation == "nan": rows[-1]["close"] = float("nan")
    if mutation == "capture": rows[-1]["capture_contract_version"] = None
    assert closed_di_candidates(rows, CONFIG, {}, now=NOW, required=31) == []


@pytest.fixture
def input_providers(monkeypatch):
    rows = closed_di_candidates(candles(), CONFIG, {"config_hash": "governed"}, now=NOW, required=31)
    di = AsyncMock(return_value={"TEST_USDT": rows})
    book = AsyncMock(return_value={"bids": [[100, 10]], "asks": [[100.1, 10]], "_observed_at": NOW.isoformat()})
    monkeypatch.setattr("app.services.l3_contract_inputs.load_closed_di_inputs", di)
    monkeypatch.setattr("app.services.market_data_service.MarketDataService.fetch_raw_orderbook", book)
    db = SimpleNamespace(begin_nested=lambda: AsyncMock())
    return db, di, book


@pytest.mark.asyncio
async def test_declared_inputs_resolve_without_changing_profile_or_upstream(input_providers):
    db, di, book = input_providers
    p, a = profile(), asset()
    before = deepcopy(p)
    prepared, = await prepare_l3_contract_inputs(db, [a], p, policy())
    ct = authorize(prepared, p)
    assert ct["authorization_status"] == "ALLOW", ct["reason_codes"]
    import json
    json.dumps(ct)  # Persisted provenance must be JSON-safe, including ingestion clocks.
    assert p == before
    assert a["indicators"]["spread_pct"] == 0.7
    assert "_indicators_by_tf" not in a
    assert prepared["_indicators_by_tf"]["1m"]["di_trend"] is True
    assert prepared["_l3_contract_ohlcv_candidates"][0]["period"] == CONFIG["adx"]["period"]
    from app.services.pipeline_rejections import evaluate_rejections
    from app.services.profile_engine import ProfileEngine
    # Real profile filters use `field`; both earlier gates must read M1 too.
    for section in ("filters", "signals"):
        for condition in p[section]["conditions"]:
            condition["field"] = condition.pop("indicator")
    prepared["di_trend"] = False  # stale flat M5 value cannot veto real M1
    prepared["taker_ratio"] = prepared["indicators"]["taker_ratio"]
    passed, rejected = evaluate_rejections([prepared], profile_config=p, stage="L3", profile_id="p")
    assert len(passed) == 1 and not rejected
    assert ProfileEngine(p).evaluate_asset(prepared)["passed_filter"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("spread,taker,expected", [(0.19, 0.5, "ALLOW"), (0.21, 0.5, "STRATEGY_BLOCK"),
                                                  (0.19, 0.49, "STRATEGY_BLOCK")])
async def test_real_threshold_boundaries_remain_enforced(input_providers, spread, taker, expected):
    db, _, book = input_providers
    book.return_value = {"bids": [[100 - spread, 1]], "asks": [[100, 1]], "_observed_at": NOW.isoformat()}
    a = asset()
    a["indicators"]["taker_ratio"] = taker
    a["_l3_live_order_flow_snapshot"]["values"]["taker_ratio"] = taker
    prepared, = await prepare_l3_contract_inputs(db, [a], profile(), policy())
    ct = authorize(prepared, profile())
    assert ct["authorization_status"] == expected, ct["reason_codes"]
    if taker < 0.5:
        assert any(c["condition_id"] == "taker" and c["status"] == "FAIL"
                   for c in ct["sections"]["entry_triggers"]["conditions"])


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["book_missing", "book_stale", "di_missing", "flow_missing"])
async def test_missing_or_stale_required_evidence_never_authorizes(input_providers, failure):
    db, di, book = input_providers
    p, a = profile(), asset()
    # Make the tested inputs required, proving no optional shortcut was introduced.
    p["filters"]["conditions"][0]["required"] = True
    p["signals"]["conditions"][0]["required"] = True
    if failure == "book_missing": book.return_value.pop("_observed_at")
    if failure == "book_stale": book.return_value["_observed_at"] = (NOW - timedelta(hours=1)).isoformat()
    if failure == "di_missing": di.return_value = {}
    if failure == "flow_missing": a.pop("_l3_live_order_flow_snapshot")
    prepared, = await prepare_l3_contract_inputs(db, [a], p, policy())
    assert authorize(prepared, p)["authorization_status"] == "CONTRACT_REJECT"


@pytest.mark.asyncio
async def test_non_allowlisted_profile_does_not_fetch_or_change_assets(input_providers):
    db, di, book = input_providers
    pol = policy()
    pol["profile_id"] = "another"
    assets = [asset()]
    assert await prepare_l3_contract_inputs(db, assets, profile(), pol) is assets
    di.assert_not_called()
    book.assert_not_called()
