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
    stored = {k: v for k, v in CFG.items() if k != "feature_groups"}   # manifests saved before v1.18
    assert pc.feature_names(stored) == BASE
    assert pc.feature_names({**CFG, "feature_groups": []}) == BASE
    assert pc.feature_names(CFG) == BASE + pc.GROUP_FEATURES["candle_structure"]   # default since v1.19


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

    async def fake(conn, owner, c, diag, deadline, progress=None):
        partial = {"horizon_minutes": 15, "variants": {"base": {"day_auc_median": 0.55}}}
        await progress(partial)                                   # persisted while running
        return partial

    monkeypatch.setattr(pc, "run_candle_ablation", fake)
    out = asyncio.run(daily.run_owner(Conn(), UUID(int=1), horizons=[15], force=True, family="candle_ablation"))
    assert out["status"] == "ablation" and out["applied_delta"] == 0 and out["auto_promotion"] is False
    assert out["ablation"]["variants"]["base"]["day_auc_median"] == 0.55
    lock = [a for q, a in calls if "pg_advisory_unlock" in q][0]
    assert lock[0] == f"pump_ml:candle_ablation:{UUID(int=1)}"
    assert not any("pump_ml_experiments" in q for q, _ in calls)
    progress_sql = [a for q, a in calls if "payload = payload ||" in q]
    assert progress_sql and progress_sql[0][1]["ablation"]["variants"]["base"]["day_auc_median"] == 0.55


def test_feature_group_and_ablation_config_is_validated():
    cd = eng.config(None)["research"]["candle"]
    assert cd["feature_groups"] == ["candle_structure"] and cd["ablation"]["groups"] == list(pc.GROUPS)
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


def _ablation_env(monkeypatch, wf_calls):
    from datetime import datetime, timezone
    from app.services import pump_directional_research as dr
    closes = synthetic(n=900); bars = bars_for(closes)

    async def fake_load(conn, symbols, tf, lo, hi):
        return closes, bars

    def fake_wf(rows, *, columns, spec, options, relative):
        wf_calls.append(len(columns))
        return {"scored_days": 2, "folds": [{"day": "d1", "auc": 0.55}, {"day": "d2", "auc": 0.56}],
                "pooled": {"day_auc_median": 0.555, "days_auc_above_half": 2}}

    class Conn:
        async def fetch(self, sql, *args):
            return [{"symbol": s} for s in closes]

    monkeypatch.setattr(pc, "load_bars", fake_load)
    monkeypatch.setattr(dr, "walk_forward_evaluation", fake_wf)
    c = eng.config(None)
    c["research"]["candle"]["ablation"] = {**c["research"]["candle"]["ablation"], "max_rows": 2000}
    return Conn(), c


def test_ablation_persists_progress_after_every_variant(monkeypatch):
    from datetime import datetime, timedelta, timezone
    wf_calls, saved = [], []
    conn, c = _ablation_env(monkeypatch, wf_calls)

    async def progress(partial):
        saved.append(json.loads(json.dumps(partial, default=str)))

    out = asyncio.run(pc.run_candle_ablation(conn, UUID(int=1), c, {}, datetime.now(timezone.utc) + timedelta(hours=1),
                                             progress=progress))
    names = ["base", *c["research"]["candle"]["ablation"]["groups"], "all"]
    assert list(out["variants"]) == names and len(wf_calls) == len(names)
    assert len(saved) == 1 + len(names)                          # once after setup, then after each variant
    assert list(saved[2]["variants"]) == ["base", "btc_beta"]    # partial results survive a later kill
    assert {"load_seconds", "frame_seconds", "rows_seconds"} <= set(out["timings"])
    assert out["variants"]["btc_beta"]["vs_base"]["days"] == 2


def test_ablation_marks_variants_that_do_not_fit_the_budget(monkeypatch):
    from datetime import datetime, timezone
    wf_calls = []
    conn, c = _ablation_env(monkeypatch, wf_calls)
    out = asyncio.run(pc.run_candle_ablation(conn, UUID(int=1), c, {}, datetime.now(timezone.utc)))
    assert wf_calls == [] and all(v == {"blocked_reason": "runtime_budget_exhausted"} for v in out["variants"].values())


def test_every_final_status_is_allowed_by_the_database_constraint():
    """2026-10-08: 'ablation' violated pump_job_status and runs stayed 'running'."""
    import re
    from pathlib import Path
    versions = Path(__file__).resolve().parents[1] / "alembic" / "versions"
    latest = (versions / "242_pump_job_status_ablation.py").read_text()
    allowed = set(re.search(r'STATUSES = \(([^)]*)\)', latest).group(1).replace('"', "").replace(" ", "").split(","))
    src = (Path(__file__).resolve().parents[1] / "app" / "services" / "pump_ml_daily.py").read_text()
    # statuses that reach the table: INSERT 'running', every ``outcome = {"status": ...}`` and the fallback
    produced = set(re.findall(r'outcome = \{"status": "(\w+)"', src)) | {"challenger", "blocked", "running", "failed"}
    assert "ablation" in produced
    assert produced <= allowed, produced - allowed


def test_finalize_failure_never_leaves_the_run_running(monkeypatch):
    calls = []

    class Conn:
        async def fetchval(self, sql, *args):
            if "pg_try_advisory_lock" in sql: return True
            if "SELECT run_id" in sql: return None
            if "config_json" in sql: return {"enabled": True, "training_job_enabled": True}
            return 0
        async def execute(self, sql, *args):
            calls.append((sql, args))
            if "SET finished_at=now(),status=$2" in sql:
                raise RuntimeError("check violation")

    async def fake(conn, owner, c, diag, deadline, progress=None):
        return {"variants": {}}

    monkeypatch.setattr(pc, "run_candle_ablation", fake)
    out = asyncio.run(daily.run_owner(Conn(), UUID(int=1), horizons=[15], force=True, family="candle_ablation"))
    assert out["status"] == "failed" and out["finalize_error"] == "RuntimeError"
    fallback = [a for q, a in calls if "status='failed'" in q]
    assert fallback and fallback[0][1]["intended_status"] == "ablation"
