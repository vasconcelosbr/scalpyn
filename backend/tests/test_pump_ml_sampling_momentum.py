"""Pump ML v1.9 (2026-10-07): per-minute sampling cap and the derived previous-window
relative residual / 24h beta — point-in-time and identical in training and live."""
import asyncio
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from app.services import pump_ml_inference as inf
from app.services import pump_ml_selection as sel
from app.services import pump_opportunity_engine as eng
from app.services.pump_contracts import CONTEXT_FEATURE_SPEC

T0 = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)


def test_cap_per_minute_is_deterministic_and_persists_across_batches():
    recs = [{"observation_id": f"id{i}", "decision_at": T0 + timedelta(seconds=i % 50)} for i in range(40)]
    recs += [{"observation_id": f"jd{i}", "decision_at": T0 + timedelta(minutes=1)} for i in range(3)]
    counts = {}
    kept = sel.cap_per_minute(recs, counts, 5)
    assert len(kept) == 5 + 3
    again = sel.cap_per_minute(recs, {}, 5)
    assert again == kept                                         # deterministic (hash order)
    more = [{"observation_id": "late", "decision_at": T0 + timedelta(seconds=55)}]
    assert sel.cap_per_minute(more, counts, 5) == []             # minute already full across batches
    assert len(sel.cap_per_minute(recs, {}, 0)) == 43            # 0 = no cap
    assert "decision_at FROM" in sel.WINDOW_IDS_SQL


def _closes(n=400, step=300, seed=5):
    rng = np.random.default_rng(seed)
    t0 = 1_790_000_000
    mkt = rng.normal(scale=0.3, size=n)
    out = {}
    for sym, b in (("A_USDT", 1.8), ("B_USDT", 1.0), ("C_USDT", 0.4), ("D_USDT", 1.1)):
        p, series = 100.0, {}
        for i in range(n):
            p *= 1 + (b * mkt[i] + rng.normal(scale=0.05)) / 100
            series[t0 + i * step] = p
        out[sym] = series
    return out, t0, step


def test_previous_window_return_uses_only_closed_candles():
    closes, t0, step = _closes()
    dec = t0 + 350 * step                       # candles opened up to dec-300 are closed
    f = sel.relative_features(closes, {("A_USDT", dec)}, step_seconds=step, window=288, min_points=200)
    a = closes["A_USDT"]
    expected = (a[dec - step] / a[dec - 4 * step] - 1) * 100
    assert f[("A_USDT", dec)]["r_prev"] == pytest.approx(expected)
    future = {s: {t: (c * 2 if t >= dec else c) for t, c in v.items()} for s, v in closes.items()}
    g = sel.relative_features(future, {("A_USDT", dec)}, step_seconds=step, window=288, min_points=200)
    assert g == f


def test_residual_prev_removes_beta_times_market():
    feats = {"A": {"beta": 2.0, "r_prev": -2.0}, "B": {"beta": 1.0, "r_prev": -1.0},
             "C": {"beta": 0.5, "r_prev": -0.5}, "D": {"beta": 1.0, "r_prev": 0.0}}
    r = sel.residual_prev(feats)
    # median r_prev = -0.75; residuals A -0.5, B -0.25, C -0.125, D +0.75 → median -0.1875
    assert r["D"] == pytest.approx(0.75 + 0.1875) and r["A"] == pytest.approx(-0.5 + 0.1875)
    assert sel.residual_prev({"A": {"beta": 1.0, "r_prev": 1.0}}) == {}


def test_live_context_equals_training_values():
    closes, t0, step = _closes()
    dec = t0 + 360 * step
    params = {"timeframe": "5m", "window_candles": 288, "min_points": 200, "prev_candles": 3}
    syms = sorted(closes)
    feats = sel.relative_features(closes, {(s, dec) for s in syms}, step_seconds=step, window=288, min_points=200)
    train = sel.residual_prev({s: feats[(s, dec)] for s in syms})

    class DB:
        async def execute(self, stmt, binds):
            lo, hi = binds["lo"].timestamp(), binds["hi"].timestamp()
            return type("R", (), {"all": lambda self: [(s, datetime.fromtimestamp(t, timezone.utc), c)
                                                      for s in binds["s"] for t, c in closes[s].items()
                                                      if lo <= t < hi]})()

    live = asyncio.run(inf.relative_context(DB(), syms, params, dec))
    for s in syms:
        assert live[s]["rel_prev15_resid"]["value"] == pytest.approx(train[s])
        assert live[s]["beta_24h"]["value"] == pytest.approx(feats[(s, dec)]["beta"])


def test_attach_adds_derived_values_in_memory():
    t0 = datetime.fromtimestamp(1_790_000_000 + 360 * 300, timezone.utc)
    closes, _, _ = _closes()

    class Conn:
        async def fetch(self, sql, *args):
            if "FROM ohlcv" in sql:
                return [{"symbol": s, "time": datetime.fromtimestamp(t, timezone.utc), "close": c}
                        for s, v in closes.items() for t, c in v.items()]
            return [{"slot_at": t0, "symbol": s, "r": r} for s, r in
                    (("A_USDT", -1.0), ("B_USDT", -0.4), ("C_USDT", 0.1), ("D_USDT", -0.6))]

    rows = [{"slot_at": t0.isoformat(), "symbol": "A_USDT", "values": {"rsi": 50}}]
    diag = {}
    asyncio.run(sel.attach_universe_benchmark(Conn(), "u", rows, 15, "h", t0, diag,
                                              beta={"timeframe": "5m", "window_candles": 288, "min_points": 200,
                                                    "prev_candles": 3}))
    v = rows[0]["values"]
    assert v["rsi"] == 50 and v["beta_24h"] is not None and v["rel_prev15_resid"] is not None
    assert rows[0]["benchmark_assets"] == 4


def test_config_and_dictionary():
    r = eng.config(None)["research"]
    assert r["max_rows_per_minute"] == 5 and r["relative_beta"]["prev_candles"] == 3
    assert {"rel_prev15_resid", "beta_24h"} <= set(r["context_features"]) <= set(CONTEXT_FEATURE_SPEC["fields"])
    for bad in ({"max_rows_per_minute": -1}, {"relative_beta": {**r["relative_beta"], "prev_candles": 0}}):
        with pytest.raises(ValueError):
            eng.config({"research": bad})


def test_cross_section_probes_labels_by_key_in_small_chunks():
    """Regression 07/10 19:51 UTC: the JOIN form timed out (QueryCanceledError)."""
    import inspect
    assert "CROSS JOIN LATERAL" in sel.CROSS_SECTION_SQL and "LIMIT 1" in sel.CROSS_SECTION_SQL
    assert inspect.signature(sel.attach_universe_benchmark).parameters["chunk"].default <= 100
