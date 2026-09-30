import random
from copy import deepcopy

import pytest

from app.services import pump_monitor_engine as eng

# Stretched, flow strong enough to score above the cap, and no trigger fired.
BASE = {
    "delta_norm": 0.5, "buy_persistence": 0.9, "cvd_slope": 0.5, "rvol_strict": 4.0,
    "price_progress_atr": 2.0, "price_change_5m_pct": 3.0, "price_change_15m_pct": 5.0,
    "breakout_hold_ratio": 1.0, "flow_change": 0.2, "spread_pct": 0.01,
    "estimated_slippage_buy_pct": 0.01, "upper_wick_ratio": 0.1, "price_extension_atr": 3.5,
}


def _cfg(enabled=True, **veto):
    cfg = deepcopy(eng.DEFAULT_CONFIG)
    cfg["score"]["exhaustion_veto"].update(enabled=enabled, **veto)
    return cfg


def _run(values, cfg):
    cells = {k: {"value": v} for k, v in values.items()}
    row = eng.score_row(cells, cfg)
    row.update(eng.apply_exhaustion_veto(cells, row["pump_monitor_score"], cfg))
    return row, cells


def test_default_config_ships_the_veto_disabled():
    assert eng.DEFAULT_CONFIG["score"]["exhaustion_veto"]["enabled"] is False


def test_disabled_veto_never_changes_the_score():
    cfg = _cfg(enabled=False)
    rng = random.Random(7)
    for _ in range(500):
        values = {k: (None if rng.random() < 0.1 else v * rng.uniform(-1.5, 2.5)) for k, v in BASE.items()}
        plain = eng.score_row({k: {"value": v} for k, v in values.items()}, cfg)["pump_monitor_score"]
        row, cells = _run(values, cfg)
        assert row["pump_monitor_score"] == plain == row["score_pre_veto"]
        assert cells["pump_monitor_score"]["value"] == plain


def test_veto_only_lowers_the_score():
    cfg = _cfg()
    rng = random.Random(11)
    for _ in range(500):
        values = {k: (None if rng.random() < 0.1 else v * rng.uniform(-1.5, 2.5)) for k, v in BASE.items()}
        row, _ = _run(values, cfg)
        if row["score_pre_veto"] is None:
            assert row["pump_monitor_score"] is None
        else:
            assert row["pump_monitor_score"] <= row["score_pre_veto"]


def test_no_price_context_never_vetoes():
    for extension in (2.9, None):
        row, _ = _run({**BASE, "price_extension_atr": extension, "upper_wick_ratio": 0.9}, _cfg())
        assert row["exhaustion_flag"] is False and row["exhaustion_triggers"] == []
        assert row["pump_monitor_score"] == row["score_pre_veto"]
    row, _ = _run({**BASE, "price_extension_atr": None}, _cfg())
    assert "price_extension_atr" in row["exhaustion_missing"]


@pytest.mark.parametrize("override, trigger", [
    ({"upper_wick_ratio": 0.6}, "wick"),
    ({"rvol_strict": 2.5, "buy_persistence": 0.75, "price_progress_atr": 0.1}, "effort_no_progress"),
    ({"breakout_hold_ratio": 0.3, "flow_change": -0.4}, "breakout_failure"),
])
def test_each_trigger_alone_caps_the_score(override, trigger):
    row, cells = _run({**BASE, **override}, _cfg())
    assert row["exhaustion_flag"] is True and row["exhaustion_triggers"] == [trigger]
    assert row["score_pre_veto"] > 20
    assert row["pump_monitor_score"] == 20.0 == cells["pump_monitor_score"]["value"]


def test_trigger_switch_off_is_respected():
    row, _ = _run({**BASE, "upper_wick_ratio": 0.6}, _cfg(use_wick=False))
    assert row["exhaustion_flag"] is False and row["pump_monitor_score"] == row["score_pre_veto"]


def test_missing_trigger_input_counts_as_false_without_error():
    row, _ = _run({**BASE, "upper_wick_ratio": None, "breakout_hold_ratio": None, "rvol_strict": None}, _cfg())
    assert row["exhaustion_flag"] is False
    assert set(row["exhaustion_missing"]) == {"wick", "effort_no_progress", "breakout_failure"}


def test_score_already_below_cap_is_unchanged():
    values = {**BASE, "delta_norm": 0.0, "buy_persistence": 0.5, "cvd_slope": 0.0, "rvol_strict": 1.0,
              "price_progress_atr": 0.0, "price_change_5m_pct": 0.0, "price_change_15m_pct": 0.0,
              "breakout_hold_ratio": 0.3, "flow_change": -0.4}
    row, _ = _run(values, _cfg())
    assert row["exhaustion_flag"] is True
    assert row["score_pre_veto"] <= 20 and row["pump_monitor_score"] == row["score_pre_veto"]


def test_alerts_keep_using_the_shared_predicates():
    cells = {k: {"value": v} for k, v in {**BASE, "rvol_strict": 2.5, "buy_persistence": 0.75,
                                          "price_progress_atr": 0.1, "breakout_hold_ratio": 0.3,
                                          "flow_change": -0.4}.items()}
    active = {a["type"] for a in eng.evaluate_alerts(cells, {}, 0, eng.DEFAULT_CONFIG)}
    assert {"effort_no_progress", "breakout_failure"} <= active


def test_cap_outside_score_scale_is_rejected():
    with pytest.raises(ValueError):
        eng.next_config_version(None, {"score": {"exhaustion_veto": {"cap": 120}}}, changed_by="u", now_iso="t")
