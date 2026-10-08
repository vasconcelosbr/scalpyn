"""Pump ML v1.18 (2026-10-08): optional candle feature groups (point-in-time), the
feature-group ablation family (research only) and the 5-minute order-book history."""
import asyncio
import json
from copy import deepcopy
from uuid import UUID

import numpy as np
import pytest

from app.services import pump_flow_history as fh
from app.services import pump_ml_candles as pc
from app.services import pump_ml_daily as daily
from app.services import pump_opportunity_engine as eng
from tests.test_pump_ml_candles import CFG, STEP, synthetic

BASE = ['beta_24h', 'rel_resid_1', 'rel_resid_3', 'rel_resid_6', 'rel_resid_12', 'vol_ratio', 'btc_ret_1',
        'btc_ret_3', 'lag_gap_1', 'lag_gap_3', 'mkt_ret_1', 'mkt_ret_3']


def bars_for(closes, seed=1):
    rng = np.random.default_rng(seed)
    out = {}
    for s, ser in closes.items():
        prev, b = None, {}
        for t in sorted(ser):
            c = ser[t]; o = prev or c
            b[t] = (o, max(o, c) * (1 + abs(rng.normal(0, 1e-3))), min(o, c) * (1 - abs(rng.normal(0, 1e-3))),
                    float(rng.lognormal(10, 0.5)))
            prev = c
        out[s] = b
    return out


def test_base_columns_and_order_are_unchanged_for_stored_models():
    assert pc.feature_names(CFG) == BASE
    assert pc.feature_names({**CFG, "feature_groups": []}) == BASE


def test_btc_beta_replaces_the_pool_beta_lag_gap_never_both():
    names = pc.feature_names({**CFG, "feature_groups": ["btc_beta"]})
    assert "lag_gap_1" not in names and {"beta_btc_24h", "lag_gap_btc_1", "lag_gap_btc_3"} <= set(names)
    union = pc.all_feature_names(CFG)
    assert set(BASE) <= set(union) and all(f in union for g in pc.GROUPS for f in pc.GROUP_FEATURES[g])


def test_every_group_is_point_in_time():
    closes = synthetic(n=600); bars = bars_for(closes)
    frame = pc.build_frame(closes, CFG, bars=bars, all_groups=True)
    col = 450
    cut = int(frame["grid"][col])
    fut_c = {s: {t: (c * 1.7 if t > cut else c) for t, c in v.items()} for s, v in closes.items()}
    fut_b = {s: {t: ((o * 1.7, h * 1.9, l * 1.5, vol * 9) if t > cut else (o, h, l, vol))
                 for t, (o, h, l, vol) in v.items()} for s, v in bars.items()}
    later = pc.build_frame(fut_c, CFG, bars=fut_b, all_groups=True)
    for name in pc.all_feature_names(CFG):
        np.testing.assert_allclose(frame["features"][name][:, col], later["features"][name][:, col],
                                   equal_nan=True, err_msg=name)


def test_group_values_are_sane():
    closes = synthetic(n=600); bars = bars_for(closes)
    f = pc.build_frame(closes, CFG, bars=bars, all_groups=True)["features"]
    col = 500
    btc = pc.build_frame(closes, CFG, all_groups=True)["syms"].index("BTC_USDT")
    assert f["beta_btc_24h"][btc, col] == pytest.approx(1.0)                 # BTC vs itself
    for k in ("cs_close_pos", "cs_upper_wick", "cs_lower_wick"):
        v = f[k][:, col]; assert np.all((v >= 0) & (v <= 1))
    assert np.all(np.abs(f["cs_body"][:, col]) <= 1)
    assert 0.3 < np.nanmedian(f["vol_rel_1"][:, col]) < 3
    r = f["pool_rank_3"][:, col]; assert np.all((r > 0) & (r <= 1))
    assert np.all(f["pool_breadth_3"][:, col] == f["pool_breadth_3"][0, col])  # same for every asset
    no_bars = pc.build_frame(closes, CFG, all_groups=True)["features"]
    assert np.all(np.isnan(no_bars["cs_body"][:, col])) and np.all(np.isnan(no_bars["vol_rel_1"][:, col]))


def test_live_cells_match_training_values_with_bars():
    closes = synthetic(n=600); bars = bars_for(closes)
    cfg = {**CFG, "feature_groups": ["candle_structure", "volume", "pool_context", "btc_beta"]}
    frame = pc.build_frame(closes, cfg, bars=bars)
    col = 480
    cells = pc.live_cells(frame, cfg, int(frame["grid"][col]) + STEP + 60)
    for name in pc.feature_names(cfg):
        v = frame["features"][name][2, col]
        got = cells[frame["syms"][2]][name]["value"]
        assert (got is None and not np.isfinite(v)) or got == pytest.approx(v)


def test_paired_comparison_is_by_day():
    base = {"folds": [{"day": "d1", "auc": 0.55}, {"day": "d2", "auc": 0.56}, {"day": "d3", "auc": 0.50},
                      {"day": "d4", "skipped": "x"}]}
    var = {"folds": [{"day": "d1", "auc": 0.57}, {"day": "d2", "auc": 0.58}, {"day": "d3", "auc": 0.49},
                     {"day": "d4", "auc": 0.9}]}
    p = pc.paired_vs_base(base, var)
    assert p["days"] == 3 and p["days_better"] == 2 and p["median_auc_delta"] == pytest.approx(0.02)
    assert p["sign_test_p"] == pytest.approx(0.5)


def test_ablation_family_saves_no_model_and_reports_variants(monkeypatch):
    calls = []

    class Conn:
        async def fetchval(self, sql, *args):
            if "pg_try_advisory_lock" in sql: return True
            if "SELECT run_id" in sql: return None
            if "config_json" in sql: return {"enabled": True, "training_job_enabled": True}
            return 0
        async def execute(self, sql, *args): calls.append((sql, args))
        def transaction(self): raise AssertionError("ablation must not open the model-saving transaction")

    async def fake(conn, owner, c, diag, deadline):
        return {"horizon_minutes": 15, "variants": {"base": {"day_auc_median": 0.55}}}

    monkeypatch.setattr(pc, "run_candle_ablation", fake)
    out = asyncio.run(daily.run_owner(Conn(), UUID(int=1), horizons=[15], force=True, family="candle_ablation"))
    assert out["status"] == "ablation" and out["applied_delta"] == 0 and out["auto_promotion"] is False
    assert out["ablation"]["variants"]["base"]["day_auc_median"] == 0.55
    lock = [a for q, a in calls if "pg_advisory_unlock" in q][0]
    assert lock[0] == f"pump_ml:candle_ablation:{UUID(int=1)}"
    assert not any("pump_ml_experiments" in q for q, _ in calls)


def test_feature_group_and_ablation_config_is_validated():
    cd = eng.config(None)["research"]["candle"]
    assert cd["feature_groups"] == [] and cd["ablation"]["groups"] == list(pc.GROUPS)
    for bad in ({"feature_groups": ["rsi"]}, {"feature_groups": ["volume", "volume"]},
                {"ablation": {**cd["ablation"], "groups": []}}, {"ablation": {**cd["ablation"], "max_folds": 2}},
                {"ablation": {**cd["ablation"], "horizon_minutes": 7}}):
        with pytest.raises(ValueError):
            eng.config({"research": {"candle": {**cd, **bad}}})


def test_book_rows_take_the_open_bucket_and_skip_empty_assets():
    rows = [{"symbol": "A_USDT", "indicators": {"spread_pct": {"value": 0.05}, "bid_depth_usdt_1pct": {"value": 1e4},
                                                "estimated_slippage_buy_pct": {"value": None}}},
            {"symbol": "B_USDT", "indicators": {"rsi": {"value": 50}}}]
    now_ms = (1_790_000_100 + 125) * 1000
    out = fh.book_rows(rows, now_ms, 300)
    assert [r["s"] for r in out] == ["A_USDT"]
    r = out[0]
    assert r["b"] == 1_790_000_100 - (1_790_000_100 % 300) and r["o"] == pytest.approx(now_ms / 1000)
    assert r["sp"] == 0.05 and r["bd"] == 1e4 and r["sb"] is None
    json.dumps(out, allow_nan=False)
    assert "WHERE pump_book_5m.observed_at <= EXCLUDED.observed_at" in fh.BOOK_SQL   # never overwrite with older
