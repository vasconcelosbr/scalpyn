"""Pump Score v1.5 — spot × perpetual context and the per-asset direction arrow."""
from copy import deepcopy

import pytest

from app.services import pump_score_v1 as v1

NOW = 1_791_379_200_000  # ms


def spec(**d):
    s = deepcopy(v1.DEFAULT_V1)
    s["derivatives"].update(d)
    return s


def stats(oi=(100.0, 100.0, 101.0, 102.0), longs=(10, 10, 10), shorts=(5, 5, 5), funding="0.0001",
          short_liq=(0, 0, 0), last_ms=NOW):
    rows = []
    for i, value in enumerate(oi):
        t = (last_ms - (len(oi) - 1 - i) * 300_000) / 1000
        j = i - (len(oi) - len(longs))
        rows.append({"time": t, "open_interest_usd": value, "last_funding_rate": funding,
                     "long_taker_size": longs[j] if j >= 0 else 0, "short_taker_size": shorts[j] if j >= 0 else 0,
                     "short_liq_usd": short_liq[j] if j >= 0 else 0, "long_liq_usd": 0})
    return rows


def test_default_spec_validates():
    errors = []
    v1.validate(v1.DEFAULT_V1, errors)
    assert errors == []


def test_metrics_from_contract_stats():
    m = v1.derivative_metrics(stats(), spec(), NOW)
    assert m["available"] and m["intervals"] == 3
    assert m["perp_flow_norm"] == pytest.approx((30 - 15) / 45, abs=1e-5)
    assert m["oi_change_pct"] == pytest.approx(2.0)
    assert m["funding_rate"] == pytest.approx(0.0001)


def test_missing_stale_and_no_perpetual_are_distinct():
    assert v1.derivative_metrics(None, spec(), NOW)["reason"] == "no_data"
    assert v1.derivative_metrics([], spec(), NOW)["reason"] == "no_perpetual"
    assert v1.derivative_metrics(stats(last_ms=NOW - 3_600_000), spec(), NOW)["reason"] == "stale"


def up(**kw):
    return {"progress_atr": 0.8, "window_delta_norm": 0.2, "cvd_slope": 0.3, **kw}


def test_fragility_flags():
    s = spec()
    assert v1.derivative_flags(up(), v1.derivative_metrics(stats(), s, NOW), s) == []
    perp_led = v1.derivative_metrics(stats(longs=(30, 30, 30), shorts=(1, 1, 1)), s, NOW)
    assert "perp_led" in v1.derivative_flags(up(window_delta_norm=0.01), perp_led, s)
    hot = v1.derivative_metrics(stats(funding="0.001"), s, NOW)
    assert "funding_hot" in v1.derivative_flags(up(), hot, s)
    squeeze = v1.derivative_metrics(stats(oi=(100, 100, 100, 99.5), short_liq=(0, 0.2, 0)), s, NOW)
    assert "short_squeeze" in v1.derivative_flags(up(), squeeze, s)
    unwinding = v1.derivative_metrics(stats(oi=(100, 100, 99, 98)), s, NOW)
    assert "oi_unwinding" in v1.derivative_flags(up(), unwinding, s)
    # a falling asset is not judged here
    assert v1.derivative_flags(up(progress_atr=-0.5), hot, s) == []


def test_gate_blocks_fragile_and_passes_without_perpetual():
    s = spec()
    hot = v1.derivative_metrics(stats(funding="0.001"), s, NOW)
    g = v1.evaluate_gates(up(), "favoravel", s, hot, ["funding_hot"])[-1]
    assert g["gate"] == "derivatives_healthy" and g["result"] is False and g["reason"] == "funding_hot"
    none = v1.derivative_metrics([], s, NOW)
    assert v1.evaluate_gates(up(), "favoravel", s, none, [])[-1]["result"] is True
    strict = spec(missing_policy="fail")
    assert v1.evaluate_gates(up(), "favoravel", strict, none, [])[-1]["result"] is False
    # not collected at all → no gate (old callers unchanged)
    assert all(x["gate"] != "derivatives_healthy" for x in v1.evaluate_gates(up(), "favoravel", s))


def test_direction_arrow():
    s = spec()
    ok = v1.derivative_metrics(stats(), s, NOW)
    assert v1.direction_signal(up(), ok, [], s) == {"direction": "long", "strength": "forte", "reason": "perp_confirma"}
    assert v1.direction_signal(up(), ok, ["funding_hot"], s)["strength"] == "normal"
    assert v1.direction_signal(up(), {"available": False}, [], s)["reason"] == "so_spot"
    down = {"progress_atr": -0.6, "window_delta_norm": -0.2, "cvd_slope": -0.1}
    shorts_opening = v1.derivative_metrics(stats(longs=(5, 5, 5), shorts=(10, 10, 10)), s, NOW)
    assert v1.direction_signal(down, shorts_opening, [], s)["direction"] == "short"
    assert v1.direction_signal(down, shorts_opening, [], s)["strength"] == "forte"
    flat = {"progress_atr": 0.1, "window_delta_norm": 0.2, "cvd_slope": 0.1}
    assert v1.direction_signal(flat, ok, [], s)["direction"] == "neutral"
    assert v1.direction_signal({"progress_atr": None}, ok, [], s)["reason"] == "sem_dados"


def test_evaluate_universe_attaches_direction_and_derivatives():
    rows = [{"symbol": "NEAR_USDT", "indicators": {"window_delta_norm": {"value": 0.2}, "cvd_slope": {"value": 0.3}}}]
    out = v1.evaluate_universe(rows, {}, None, {}, minute_ms=NOW, spec=spec(),
                               derivatives={"NEAR_USDT": stats()}, now_ms=NOW)
    res = out["results"]["NEAR_USDT"]
    assert res["derivatives"]["available"] is True and "direction" in res
    assert any(g["gate"] == "derivatives_healthy" for g in res["gates"])


@pytest.mark.asyncio
async def test_load_derivatives_one_call_per_interval(monkeypatch):
    from app.services import pump_monitor_service as svc
    calls = []
    store = {}

    class Resp:
        def __init__(self, data): self._data = data
        def raise_for_status(self): pass
        def json(self): return self._data

    class Client:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url, params=None):
            calls.append((url.rsplit("/", 1)[-1], (params or {}).get("contract")))
            if url.endswith("/contracts"):
                return Resp([{"name": "NEAR_USDT"}, {"name": "OLD_USDT", "in_delisting": True}])
            return Resp(stats())

    class Redis:
        async def get(self, key): return store.get(key)
        async def set(self, key, value, ex=None): store[key] = value

    async def fake_redis(): return Redis()
    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", Client)
    monkeypatch.setattr(svc, "_redis", fake_redis)
    out = await svc.load_derivatives(["NEAR_USDT", "LEO_USDT", "OLD_USDT"], spec(), NOW, 4)
    assert out["LEO_USDT"] == [] and out["OLD_USDT"] == [] and len(out["NEAR_USDT"]) == 4
    assert calls == [("contracts", None), ("contract_stats", "NEAR_USDT")]
    await svc.load_derivatives(["NEAR_USDT"], spec(), NOW + 30_000, 4)  # same 5m slot: cached
    assert len(calls) == 2
