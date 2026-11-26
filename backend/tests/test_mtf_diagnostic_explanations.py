"""Contract tests for frozen explanations and unchanged trading decisions."""
from copy import deepcopy
import json
from unittest.mock import patch

import pytest

from app.schemas.mtf_chart import MTFChartConfig, experimental_preset
from app.schemas.mtf_rules import Condition, RuleGroup
from app.services.mtf_config_contract import complete_config
from app.services.mtf_chart_engine import evaluate
from app.services.mtf_btc_context import frozen_frame
from app.services.mtf_risk_audit import publication
from app.services.mtf_rules_engine import evaluate_group
from app.services.mtf_trigger_diagnostics import finalize_diagnostics
from app.services.profile_runtime_config import canonical_hash
from test_mtf_chart_engine import setup_frames, candle


def config():
    raw = complete_config(experimental_preset(), {"atr": {"enabled": True, "period": 14},
                          "rsi": {"enabled": True, "period": 14}})
    raw["context_audit"]["enabled"] = True
    return MTFChartConfig.model_validate(raw)


def checks(result):
    return {r["id"]: r for r in result["trigger_diagnostics"]["checks"]}


@pytest.mark.parametrize("btc,spread,missing", [("UP", .01, False), ("DOWN", .01, False),
                                               (None, None, False), ("UP", .01, True)])
def test_explanations_leave_decision_signal_identity_and_inputs_unchanged(btc, spread, missing):
    cfg = config()
    frames = {} if missing else setup_frames()
    before, config_before = deepcopy(frames), deepcopy(cfg.model_dump())
    with patch("app.services.mtf_diagnostic_explanations.explain_diagnostics"):
        baseline = evaluate("TEST_USDT", frames, cfg, 28800, spread, btc)
    result = evaluate("TEST_USDT", frames, cfg, 28800, spread, btc)
    for record in (baseline, result):
        record["config_hash"] = canonical_hash(cfg.engine_dump())
        publication(record, cfg, 28800)
    for key in ("status", "plan", "score", "vetoes", "trigger_5m", "signal_contract"):
        assert result[key] == baseline[key]
    assert frames == before and cfg.model_dump() == config_before
    assert result["config_hash"] == baseline["config_hash"]
    saved = deepcopy(result)
    cfg.pivot_left += 1
    cfg.rules.trigger.conditions.clear()
    assert result == saved
    json.dumps(result, allow_nan=False)


def test_integrity_details_preserve_the_active_break_and_closed_domain():
    cfg = config()
    frames = setup_frames()
    frames["1h"] = dict(atr=1, patterns=[], pivots=[
        dict(time=t, confirmed_at=t+1, kind=k, label=l, price=p)
        for t,k,l,p in [(0,"L","L",97),(1,"H","H",99),(2,"L","HL",99.6),(3,"H","HH",101)]],
        candles=[candle(t=21600,c=99),candle(t=25200,c=100)])
    result = evaluate("TEST_USDT", frames, cfg, 28800, .01, "UP")
    row = checks(result)["audit:zone_integrity"]
    evidence = row["explanation"]["evidence"]
    assert row["state"] == "FAIL"
    assert evidence["intact"] is False and evidence["broken_at"] == 25200
    assert evidence["last_break"]["close"] == 99
    assert evidence["last_break"]["pivot"]["price"] == 99.6
    assert row["explanation"]["window"] == dict(start_at=21600,end_at=28800,candle_count=2,scope="analysis_history")
    assert row["explanation"]["parameters"]["pivot_left"] == cfg.pivot_left
    assert row["label"] == "Integridade da estrutura do período da zona"
    # A not-yet-confirmed pivot cannot enter the frozen evidence.
    frames["1h"]["pivots"].append(dict(time=28000,confirmed_at=40000,kind="H",label="HH",price=200))
    later = evaluate("TEST_USDT", frames, cfg, 28800, .01, "UP")
    assert checks(later)["audit:zone_integrity"]["explanation"] == row["explanation"]


def test_btc_uses_its_own_window_and_never_the_asset_window():
    cfg = config()
    cfg.btc_trend_timeframe = "1h"
    result = evaluate("TEST_USDT", setup_frames(), cfg, 28800, .01, "DOWN")
    btc_frame = dict(candles=[candle(t=21600),candle(t=25200)], pivots=[])
    frozen = frozen_frame(btc_frame,"1h",28800,cfg)
    result.update(observed_at_utc="observed",config_hash="snapshot",source="gate.io",
                  btc_context=dict(symbol="BTC_USDT",source="gate.io",frames={"1h":frozen}))
    finalize_diagnostics(result,cfg,28801,28800)
    detail = checks(result)["btc_downtrend"]["explanation"]
    assert detail["source_symbol"] == "BTC_USDT"
    assert detail["window"]["candle_count"] == 2
    assert detail["window"]["start_at"] == "1970-01-01T06:00:00+00:00"
    assert detail["evidence"]["complete"] is False
    assert checks(result)["btc_downtrend"]["actual"] == "DOWN"
    assert result["trigger_diagnostics"]["explanation_context"]["config_hash"] == "snapshot"


def test_nested_trace_matches_unknown_disabled_and_alternative_states():
    root = RuleGroup.model_validate(dict(logic="OR",conditions=[
        dict(id="pass",label="Volume",left={"ref":"volume.ratio"},operator=">",right={"value":1}),
        dict(logic="AND",conditions=[
            dict(id="missing",label="Spread",left={"ref":"market.spread_pct"},operator="<",right={"value":.1}),
            dict(id="off",label="Desativada",left={"ref":"volume.ratio"},operator="<",right={"value":1},enabled=False)])]))
    tree={}
    state, rows=evaluate_group(root,{"volume":{"ratio":3}}, {},0,trace=tree)
    assert state == tree["state"] == "UNAVAILABLE"
    assert tree["children"][0] == {"check_id":"pass","state":"PASS"}
    assert tree["children"][1]["state"] == "UNAVAILABLE"
    assert tree["children"][1]["children"][1] == {"check_id":"off","state":"DISABLED"}
    assert {r["id"]:r["state"] for r in rows} == {"pass":"PASS","missing":"UNAVAILABLE","off":"DISABLED"}


def test_cross_preserves_both_operand_times_windows_and_configurations():
    rule=Condition.model_validate(dict(id="cross",label="Cruzamento",left={"ref":"indicators.fast"},
                                       operator="crosses_above",right={"ref":"indicators.slow"}))
    series = {
        "indicators.fast":[dict(value=v,timeframe="5m",close_at=t,valid_until=1000,
                               configuration={"parameters":{"periods":[3]}},
                               window=dict(start_at=t-900,end_at=t,candle_count=3))
                           for t,v in [(300,9),(600,11),(900,1)]],
        "indicators.slow":[dict(value=10,timeframe="1m",close_at=t,valid_until=1000,
                               configuration={"parameters":{"periods":[8]}},
                               window=dict(start_at=t-480,end_at=t,candle_count=8))
                           for t in (300,600,660)],
    }
    state, rows=evaluate_group(rule,{},series,700)
    assert state == "PASS"
    row=rows[0]
    assert row["operand_samples"]["right"]["close_at"] == 600  # not the newer 660 sample
    assert row["operand_samples"]["left"]["configuration"]["parameters"] == {"periods":[3]}
    assert row["operand_samples"]["right"]["window"]["candle_count"] == 8
    assert row["previous"]["left_sample"]["close_at"] == 300
    assert row["previous"]["right_sample"]["close_at"] == 300


def test_missing_indicator_retains_its_config_without_inventing_a_sample():
    cfg=config()
    cfg.rules.trigger=RuleGroup.model_validate(dict(logic="AND",conditions=[
        dict(id="rsi",label="RSI",left={"ref":"indicators.rsi_1h"},operator="between",right={"value":[40,60]})]))
    result=evaluate("TEST_USDT",setup_frames(),cfg,28800,.01,"UP")
    row=checks(result)["rsi"]
    detail=row["explanation"]["inputs"]["left"]
    assert row["state"] == "UNAVAILABLE" and row["actual"] is None
    assert detail["configuration"] == cfg.indicators["rsi_1h"].model_dump()
    assert not detail.get("window") and not detail.get("close_at")
    group=checks(result)["rules:GATILHO"]
    assert group["rule_tree"]["children"][0]["check_id"] == "rsi"
    assert group["explanation"]["operational_timeframe"] == cfg.stage("trigger")
    assert group["explanation"]["timeframes"] == ["1h"]
