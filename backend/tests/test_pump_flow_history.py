"""Pump Monitor v1.16: long-lived 5-minute flow / perpetual history (collection only),
and the 2026-10-08 candle training caps."""
import asyncio
import json
from copy import deepcopy
from datetime import datetime, timezone

import pytest

from app.services import pump_flow_history as fh
from app.services import pump_monitor_engine as me
from app.services import pump_opportunity_engine as eng


def test_flow_window_covers_the_last_two_closed_buckets():
    now_ms = (1_790_000_100 + 125) * 1000                 # 2 min 5 s into a 5-min bucket
    w = fh.flow_window(now_ms, 300)
    slot = 1_790_000_100 - (1_790_000_100 % 300)
    assert w["hi"] == datetime.fromtimestamp(slot, timezone.utc)          # current (open) bucket excluded
    assert w["lo"] == datetime.fromtimestamp(slot - 600, timezone.utc)
    assert "GROUP BY symbol, b" in fh.FLOW_5M_SQL and "ON CONFLICT (symbol, bucket_start)" in fh.FLOW_5M_SQL
    # 8 target columns ↔ 8 select expressions (computed_at = now()); validated on Postgres 16
    assert "FILTER (WHERE partial), now()" in fh.FLOW_5M_SQL


def test_perp_rows_keep_gate_values_and_skip_missing_perpetuals():
    deriv = {"B_USDT": [{"time": 1_790_000_100, "long_taker_size": "12.5", "short_taker_size": 3,
                         "open_interest_usd": 1e6, "short_liq_usd": None, "long_liq_usd": "nan",
                         "last_funding_rate": 0.0001, "lsr_taker": 1.2, "mark_price": 0.5}],
             "A_USDT": [], "C_USDT": None, "D_USDT": [{"time": None}]}
    rows = fh.perp_rows(deriv, "5m")
    assert [r["s"] for r in rows] == ["B_USDT"]                               # no perp / failed / bad time → nothing
    r = rows[0]
    assert r["t"] == 1_790_000_100 and r["i"] == "5m" and r["lts"] == 12.5 and r["sts"] == 3.0
    assert r["sl"] is None and r["ll"] is None                                 # never invented
    json.dumps(rows, allow_nan=False)


def test_write_issues_flow_and_perp_upserts():
    calls = []

    class Res:
        rowcount = 7

    class DB:
        async def execute(self, stmt, params):
            calls.append((str(stmt), params))
            return Res()

    out = asyncio.run(fh.write(DB(), ["B_USDT", "A_USDT"], {"A_USDT": [{"time": 1_790_000_100}]},
                               1_790_000_400_000, {"step_seconds": 300}, "5m"))
    assert out == {"flow": 7, "perp": 7}
    assert "pump_flow_5m" in calls[0][0] and calls[0][1]["s"] == ["A_USDT", "B_USDT"] and calls[0][1]["step"] == 300
    assert "pump_perp_stats_5m" in calls[1][0] and json.loads(calls[1][1]["batch"])[0]["s"] == "A_USDT"
    calls.clear()
    asyncio.run(fh.write(DB(), [], {}, 0, {"step_seconds": 300}, "5m"))
    assert calls == []                                                         # nothing to write → no SQL


def test_flow_history_config_is_validated():
    c = me.DEFAULT_CONFIG["flow_history"]
    assert c == {"enabled": True, "step_seconds": 300, "retention_days": 180}
    for bad in ({"step_seconds": 90}, {"retention_days": 0}):
        body = deepcopy(me.DEFAULT_CONFIG); body["flow_history"] = {**c, **bad}
        with pytest.raises(ValueError):
            me.validate_config(body)
    me.validate_config(deepcopy(me.DEFAULT_CONFIG))


def test_candle_training_uses_more_rows_and_trains_the_applied_horizon_first():
    cd = eng.config(None)["research"]["candle"]
    assert cd["max_rows"] == 100000 and cd["max_rows_per_time"] == 12
    assert cd["horizons_minutes"][0] == 15                                    # applied horizon never cut by budget
    assert cd["compare_label_modes"] == ["path_mean"]                         # comparison done; halves runtime
