"""User-owned declarative MTF rules: compatibility, effective edits and causality."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.schemas.mtf_chart import MTFChartConfig, experimental_preset
from app.schemas.mtf_rules import Condition, RuleGroup
from app.services.mtf_config_contract import complete_config
from app.services.mtf_chart_engine import evaluate, simulate_step
from app.services.mtf_chart_v3 import calculate_frames
from app.services.mtf_rules_engine import evaluate_group
from test_mtf_chart_engine import setup_frames, candle, pending


@pytest.fixture
def raw():
    return complete_config(
        experimental_preset(),
        {
            "atr": {"enabled": True, "period": 14},
            "ema": {"enabled": True, "periods": [9, 21]},
            "rsi": {"enabled": True, "period": 14},
        },
    )


def condition(key, ref, value, operator=">="):
    return dict(
        id=key, label=key, left={"ref": ref}, operator=operator, right={"value": value}
    )


def test_conversion_preserves_original_and_decision(raw):
    original = experimental_preset()
    before = deepcopy(original)
    inherited = {"atr": {"enabled": True, "period": 14}}
    inherited_before = deepcopy(inherited)
    complete_config(original, inherited)
    assert original == before and inherited == inherited_before
    frames = setup_frames()
    old = evaluate(
        "BTC_USDT", frames, MTFChartConfig.model_validate(original), 28800, 0.01, "UP"
    )
    new = evaluate(
        "BTC_USDT", frames, MTFChartConfig.model_validate(raw), 28800, 0.01, "UP"
    )
    for key in ("status", "score", "plan", "vetoes"):
        assert new[key] == old[key]
    assert new["operational_effect"] is False


def test_custom_trigger_veto_score_and_missing_data(raw):
    frames = setup_frames()
    frames["5m"]["patterns"] = []
    raw["rules"]["trigger"]["conditions"] = [
        condition("spread_trigger", "market.spread_pct", 0.1, "<")
    ]
    raw["min_score"] = 0
    raw["rules"]["scoring"] = [
        dict(
            id="tight",
            points=3,
            when={
                "logic": "AND",
                "conditions": [
                    condition("tight_spread", "market.spread_pct", 0.1, "<")
                ],
            },
        )
    ]

    def run():
        return evaluate(
            "BTC_USDT", frames, MTFChartConfig.model_validate(raw), 28800, 0.01, "UP"
        )

    result = run()
    assert result["status"] == "TRIGGERED" and result["plan"]
    assert result["score"]["components"]["rule:tight"] == 3
    assert (
        next(c for c in result["trigger_diagnostics"]["checks"] if c["id"] == "plan")[
            "state"
        ]
        == "PASS"
    )
    raw["rules"]["vetoes"] = [condition("too_tight", "market.spread_pct", 0.1, "<")]
    assert "RULE_VETO:too_tight" in run()["vetoes"]
    raw["rules"]["vetoes"] = [condition("rsi_missing", "indicators.rsi_1h", 50)]
    assert "RULE_DATA_UNAVAILABLE:rsi_missing" in run()["vetoes"]


def test_nested_groups_disabled_and_unknown():
    a = condition("a", "volume.ratio", 2)
    b = condition("b", "market.spread_pct", 0.1, "<")
    group = RuleGroup.model_validate(
        {"logic": "OR", "conditions": [a, {"logic": "AND", "conditions": [b]}]}
    )
    assert (
        evaluate_group(
            group, {"volume": {"ratio": 3}, "market": {"spread_pct": 0.2}}, {}, 0
        )[0]
        == "PASS"
    )
    assert evaluate_group(group, {"volume": {"ratio": 3}}, {}, 0)[0] == "UNAVAILABLE"
    b["enabled"] = False
    group = RuleGroup.model_validate({"logic": "AND", "conditions": [a, b]})
    assert evaluate_group(group, {"volume": {"ratio": 3}}, {}, 0)[0] == "PASS"


def test_cross_alignment_excludes_future_and_gaps():
    rule = Condition.model_validate(
        condition("cross", "indicators.fast", 10, "crosses_above")
    )
    series = {
        "indicators.fast": [
            dict(value=v, close_at=t, timeframe="5m", valid_until=t + 600)
            for t, v in [(300, 9), (600, 11), (900, 5)]
        ]
    }
    assert evaluate_group(rule, {}, series, 600)[0] == "PASS"
    assert evaluate_group(rule, {}, series, 900)[0] == "FAIL"
    assert evaluate_group(rule, {}, series, 1600)[0] == "UNAVAILABLE"
    series["indicators.fast"].pop(0)
    assert evaluate_group(rule, {}, series, 600)[0] == "UNAVAILABLE"


def test_flow_is_context_until_referenced_and_partial_is_unavailable(raw):
    cfg = MTFChartConfig.model_validate(raw)
    changed = deepcopy(raw)
    changed["pump_flow"]["asset_min_delta"] = 0.5
    assert cfg.engine_dump() == MTFChartConfig.model_validate(changed).engine_dump()
    raw["pump_flow"]["enabled"] = True
    raw["rules"]["trigger"]["conditions"] = [
        condition("flow", "flow.capital.1h.ratio", 0.01)
    ]
    cfg = MTFChartConfig.model_validate(raw)
    changed = deepcopy(raw)
    changed["pump_flow"]["asset_min_delta"] = 0.5
    assert cfg.engine_dump() != MTFChartConfig.model_validate(changed).engine_dump()
    context = {
        "confirmation": "confirmed",
        "capital": {"1h": {"state": "unavailable", "ratio": 0.5}},
    }
    result = evaluate("BTC_USDT", setup_frames(), cfg, 28800, 0.01, "UP", flow=context)
    assert "RULE_DATA_UNAVAILABLE:flow" in result["vetoes"]
    context["capital"]["1h"]["state"] = "inflow"
    assert (
        evaluate("BTC_USDT", setup_frames(), cfg, 28800, 0.01, "UP", flow=context)[
            "status"
        ]
        == "TRIGGERED"
    )


def test_local_indicator_parameters_and_closed_cutoff(raw):
    # Small explicit windows make this fixture independent of producer defaults.
    for instance in raw["indicators"].values():
        instance["sample_window"] = 30
    raw["indicators"]["short_rsi"] = dict(
        raw["indicators"]["rsi_5m"], parameters={"period": 3}, display=True
    )
    cfg = MTFChartConfig.model_validate(raw)
    from app.schemas.mtf_rules import DURATIONS
    import math

    market = {
        tf: [
            candle(t=i * DURATIONS[tf], o=100 + math.sin(i), c=100 + math.sin(i + 1))
            for i in range(60)
        ]
        for tf in cfg.active_timeframes()
    }
    cutoff = 60 * DURATIONS["4h"]
    frames, series, errors = calculate_frames(market, cfg, cutoff)
    assert not errors
    assert (
        series["indicators.short_rsi"][-1]["value"]
        != series["indicators.rsi_5m"][-1]["value"]
    )
    assert frames["5m"]["atr"] == series["indicators.atr_5m"][-1]["value"]
    cutoff = 55 * 300
    _, series, _ = calculate_frames(market, cfg, cutoff)
    assert all(p["close_at"] <= cutoff for points in series.values() for p in points)


@pytest.mark.parametrize(
    "edit",
    [
        lambda r: r["indicators"]["rsi_1h"]["parameters"].update(ignored=1),
        lambda r: r["indicators"]["rsi_1h"].update(timeframe="2m"),
        lambda r: r["rules"]["armed"]["conditions"][0]["left"].update(
            ref="unknown.value"
        ),
        lambda r: r["frame_atr"].update({"1h": "rsi_1h"}),
        lambda r: r["indicators"]["rsi_1h"].update(sample_window=2),
    ],
)
def test_invalid_contract_rejected(raw, edit):
    edit(raw)
    with pytest.raises(ValueError):
        MTFChartConfig.model_validate(raw)


def test_stage_timeframe_changes_simulation_clock(raw):
    raw["timeframes"]["trigger"] = "1h"
    cfg = MTFChartConfig.model_validate(raw)
    state = {**pending(), "observed_at": 3610, "expires_at": 18000}
    result = simulate_step(state, candle(t=7200, o=100, h=101, l=99.5, c=100), cfg)
    assert result["status"] == "OPEN"
    assert (
        simulate_step(result, candle(t=10800, o=100, h=101, l=99.5, c=100), cfg)[
            "status"
        ]
        == "OPEN"
    )
    assert (
        simulate_step(result, candle(t=14400, o=100, h=101, l=99.5, c=100), cfg)[
            "reason"
        ]
        == "CANDLE_GAP"
    )


@pytest.mark.asyncio
async def test_validate_is_read_only_and_checks_ownership(raw, monkeypatch):
    from app.api import mtf_chart

    monkeypatch.setattr(
        mtf_chart.service,
        "watchlist_options",
        AsyncMock(return_value=[{"id": "l3-consolidated"}]),
    )
    write = AsyncMock()
    monkeypatch.setattr(mtf_chart.config_service, "update_config", write)
    db = SimpleNamespace(commit=AsyncMock())
    result = await mtf_chart.validate_config(
        MTFChartConfig.model_validate(raw), db, uuid4()
    )
    assert (
        result["valid"]
        and result["config"] == MTFChartConfig.model_validate(raw).model_dump()
    )
    write.assert_not_called()
    db.commit.assert_not_called()
    raw["monitored_watchlists"].append(str(uuid4()))
    from fastapi import HTTPException

    with pytest.raises(HTTPException):
        await mtf_chart.validate_config(MTFChartConfig.model_validate(raw), db, uuid4())


def test_disabled_veto_and_indicator_do_not_block(raw):
    raw["indicators"]["rsi_1h"]["enabled"] = False
    raw["rules"]["vetoes"] = [
        {**condition("disabled", "indicators.rsi_1h", 50), "enabled": False}
    ]
    cfg = MTFChartConfig.model_validate(raw)
    result = evaluate("BTC_USDT", setup_frames(), cfg, 28800, 0.01, "UP")
    assert result["status"] == "TRIGGERED"
    assert (
        next(
            c
            for c in result["trigger_diagnostics"]["checks"]
            if c["id"] == "rules:VETO 1"
        )["state"]
        == "DISABLED"
    )


def test_types_unknowns_and_nonfinite_parameters_rejected(raw):
    for rule in [
        condition("bad", "structure.trend", 2),
        condition("bad", "trigger.present", True, ">"),
    ]:
        candidate = deepcopy(raw)
        candidate["rules"]["trigger"]["conditions"] = [rule]
        with pytest.raises(ValueError):
            MTFChartConfig.model_validate(candidate)
    raw["indicators"]["rsi_1h"]["parameters"]["period"] = float("inf")
    with pytest.raises(ValueError):
        MTFChartConfig.model_validate(raw)


def test_consolidated_accepts_version_and_trigger_clock(raw):
    from test_l3_armed_observation import context
    from app.services.l3_armed_observation import reading
    from app.services.mtf_indicator_config import mtf_indicator_identity
    from app.services.profile_runtime_config import canonical_hash
    from datetime import timedelta

    uid, _, _, item, row, kwargs = context()
    raw["timeframes"]["trigger"] = "1h"
    cfg = MTFChartConfig.model_validate(raw)
    identity = mtf_indicator_identity(cfg, uid)
    kwargs.update(cfg=cfg, indicator_identity=identity)
    kwargs["now"] += timedelta(minutes=30)
    row["analysis"].update(
        engine_version=cfg.schema_version,
        config_hash=canonical_hash(cfg.engine_dump()),
        indicator_identity=identity,
    )
    assert reading(item, row, **kwargs)["status"] == "ARMED"
    kwargs["now"] += timedelta(hours=3)
    assert "EVALUATION_STALE" in reading(item, row, **kwargs)["reason_codes"]


@pytest.mark.asyncio
async def test_v3_chart_uses_owned_contract_without_global_indicator_reads(
    raw, monkeypatch
):
    from app.services import mtf_chart_v3 as service, mtf_chart_service as chart
    from app.services.market_data_service import market_data_service
    from app.services.mtf_indicator_config import mtf_indicator_identity

    cfg = MTFChartConfig.model_validate(raw)
    frames = setup_frames()
    monkeypatch.setattr(service.time, "time", lambda: 28801)
    monkeypatch.setattr(
        chart,
        "market_candles",
        AsyncMock(side_effect=lambda symbol, tf, cfg: frames[tf]["candles"]),
    )
    monkeypatch.setattr(
        service, "calculate_frames", lambda *args: (deepcopy(frames), {}, {})
    )
    monkeypatch.setattr(
        service, "load_context", AsyncMock(return_value={"confirmation": "disabled"})
    )
    monkeypatch.setattr(
        market_data_service,
        "fetch_orderbook_metrics",
        AsyncMock(return_value={"market_data_source": "gate.io", "spread_pct": 0.01}),
    )
    monkeypatch.setattr(
        chart,
        "profile_context",
        AsyncMock(side_effect=AssertionError("global configuration must not be read")),
    )
    uid = uuid4()
    data = await chart.chart_data(
        object(), uid, "BTC_USDT", None, cfg, include_display=False
    )
    assert data["analysis"]["status"] == "TRIGGERED"
    assert data["analysis"]["engine_version"] == "mtf_chart_v3"
    assert data["analysis"]["indicator_identity"] == mtf_indicator_identity(cfg, uid)
    assert data["indicator_config"] == {
        k: v.model_dump() for k, v in cfg.indicators.items()
    }
    other = await chart.chart_data(
        object(), uuid4(), "BTC_USDT", None, cfg, include_display=False
    )
    assert (
        other["analysis"]["indicator_identity"]
        != data["analysis"]["indicator_identity"]
    )


def test_conversion_retains_rsi_variants_and_adx_acceleration():
    import math
    from app.schemas.mtf_rules import DURATIONS

    raw = complete_config(
        experimental_preset(),
        {
            "atr": {"enabled": True, "period": 14},
            "rsi": {"enabled": True, "period": 14, "periods": [6, 12, 24]},
            "adx": {"enabled": True, "period": 14},
        },
    )
    for instance in raw["indicators"].values():
        instance["sample_window"] = 30
    cfg = MTFChartConfig.model_validate(raw)
    market = {
        tf: [
            candle(
                t=i * DURATIONS[tf],
                h=103 + math.sin(i),
                l=98 + math.sin(i),
                c=100 + math.sin(i),
            )
            for i in range(35)
        ]
        for tf in cfg.active_timeframes()
    }
    frames, series, errors = calculate_frames(market, cfg, 35 * DURATIONS["4h"])
    assert not errors
    assert all(
        frames[tf]["indicators"].get(name)
        for tf in cfg.active_timeframes()
        for name in ("rsi", "rsi_6", "rsi_12", "rsi_24", "adx_acceleration")
    )
    assert series["indicators.rsi_6_1h"][-1]["configuration"]["parameters"] == {
        "period": 6
    }
