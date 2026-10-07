"""Pump Score v1.4 — capital-flow regime (universe USDT taker flow)."""
from copy import deepcopy
from datetime import datetime, timedelta

import pytest

from app.services import pump_score_v1 as v1
from app.services import pump_monitor_service as svc


def spec(**cf):
    s = deepcopy(v1.DEFAULT_V1)
    s["capital_flow"].update(cf)
    return s


def minutes(buy, sell, n=15, symbols=50, complete=50):
    return [{"minute_ms": i * 60_000, "buy_usdt": buy, "sell_usdt": sell,
             "symbols": symbols, "complete_symbols": complete} for i in range(n)]


def test_default_spec_validates():
    errors = []
    v1.validate(v1.DEFAULT_V1, errors)
    assert errors == []


@pytest.mark.parametrize("buy,sell,state", [
    (120, 80, "forte_entrada"),   # ratio 0.20
    (104, 96, "entrada"),         # ratio 0.04
    (100, 100, "neutro"),
    (96, 104, "saida"),
    (80, 120, "forte_saida"),
])
def test_absolute_levels_during_warmup(buy, sell, state):
    out = v1.capital_flow(minutes(buy, sell), None, spec())
    assert out["state"] == state and out["method"] == "absolute_ratio"
    assert out["net_usdt"] == round((buy - sell) * 15, 2)
    assert out["enter_score_delta"] == spec()["capital_flow"]["enter_score_delta"][state]


def test_z_score_after_warmup():
    stats = {"mean": 0.0, "var": 0.0004, "n": 1000}  # std 0.02
    out = v1.capital_flow(minutes(102, 98), stats, spec())  # ratio 0.02 → z 1.0
    assert out["method"] == "z_score" and out["z"] == pytest.approx(1.0) and out["state"] == "entrada"


def test_thin_or_missing_data_is_unknown_never_guessed():
    assert v1.capital_flow(minutes(120, 80, complete=10), None, spec())["state"] == "desconhecido"
    assert v1.capital_flow(minutes(120, 80, n=5), None, spec())["state"] == "desconhecido"
    assert v1.capital_flow([], None, spec())["state"] == "desconhecido"
    assert v1.capital_flow(minutes(120, 80), None, spec(enabled=False))["state"] == "desligado"


def test_cap_only_worsens_regime():
    out = v1.capital_flow(minutes(80, 120), None, spec())
    assert v1.apply_capital_cap("favoravel", out) == "desfavoravel"
    assert v1.apply_capital_cap("desfavoravel", out) == "desfavoravel"
    inflow = v1.capital_flow(minutes(120, 80), None, spec())
    assert v1.apply_capital_cap("desfavoravel", inflow) == "desfavoravel"
    assert v1.apply_capital_cap("desconhecido", out) == "desconhecido"


def test_enter_score_delta_is_clamped():
    s = spec()
    assert v1.effective_stability(s, {"enter_score_delta": 10})["stability"]["enter_score"] == 60
    s["stability"]["stay_score"] = 48
    assert v1.effective_stability(s, {"enter_score_delta": -5})["stability"]["enter_score"] == 48
    assert v1.effective_stability(s, {"enter_score_delta": 0}) is s


def test_evaluate_universe_reports_capital_and_updates_stats_once_per_minute():
    state = {}
    out = v1.evaluate_universe([], {}, None, state, minute_ms=60_000, spec=spec(),
                               capital_minutes=minutes(80, 120))
    reg = out["regime"]
    assert reg["capital"]["state"] == "forte_saida" and reg["enter_score"] == 60
    assert state["capital_stats"]["n"] == 1
    v1.evaluate_universe([], {}, None, state, minute_ms=60_000, spec=spec(), capital_minutes=minutes(80, 120))
    assert state["capital_stats"]["n"] == 1  # same minute: no second update


def test_validation_rejects_bad_levels():
    s = spec(z_levels={"forte_entrada": 0.5, "entrada": 1.0, "saida": -0.5, "forte_saida": -1.5})
    errors = []
    v1.validate(s, errors)
    assert any("z_levels" in e for e in errors)


def test_capital_minutes_counts_only_complete_usdt_buckets():
    s = spec(window_minutes=2, exclude_symbols=["XRP_USDT"])
    last = 120_000
    buckets = {
        "BTC_USDT": {60_000: {"buy_quote": 10, "sell_quote": 4, "partial": False},
                     120_000: {"buy_quote": 5, "sell_quote": 5, "partial": True}},
        "ETH_USDT": {120_000: {"buy_quote": 3, "sell_quote": 1, "partial": False}},
        "XRP_USDT": {120_000: {"buy_quote": 100, "sell_quote": 0, "partial": False}},
        "ETH_BTC": {120_000: {"buy_quote": 100, "sell_quote": 0, "partial": False}},
    }
    out = svc.capital_minutes(buckets, ["BTC_USDT", "ETH_USDT", "XRP_USDT", "ETH_BTC"], last, s)
    assert [m["minute_ms"] for m in out] == [60_000, 120_000]
    assert out[0] == {"minute_ms": 60_000, "buy_usdt": 10.0, "sell_usdt": 4.0, "symbols": 2, "complete_symbols": 1}
    assert out[1]["buy_usdt"] == 3.0 and out[1]["complete_symbols"] == 1


@pytest.mark.asyncio
async def test_history_binds_typed_params_and_ranks_hours():
    calls = {}

    class Result:
        def mappings(self):
            return self

        def all(self):
            return [
                {"local_hour": datetime(2026, 10, 7, 9), "buy": 130, "sell": 70, "net": 60, "minutes": 60, "coverage": 0.9},
                {"local_hour": datetime(2026, 10, 7, 10), "buy": 60, "sell": 140, "net": -80, "minutes": 60, "coverage": 0.9},
                {"local_hour": datetime(2026, 10, 7, 11), "buy": 100, "sell": 100, "net": 0, "minutes": 10, "coverage": 0.9},
            ]

    class Db:
        async def execute(self, stmt, params):
            calls.update(params)
            return Result()

    out = await svc.capital_flow_history(Db(), "00000000-0000-0000-0000-000000000001", days=7, top=1,
                                         tz_offset_minutes=-180)
    # asyncpg binds interval/timestamptz only from timedelta/datetime (2026-10-05 incident)
    assert isinstance(calls["off"], timedelta) and isinstance(calls["since"], datetime)
    assert out["top_inflow"][0]["hour"] == "2026-10-07T09:00" and out["top_outflow"][0]["hour"] == "2026-10-07T10:00"
    assert {h["hour"] for h in out["hour_of_day"]} == {9, 10}  # the 10-minute hour is excluded
