"""Pump ML v1.20 (2026-10-08): spot taker-flow imbalance from point-in-time snapshots,
usable-history gate and the pre-registered ablation (research only, never applied)."""
import asyncio
import json
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from uuid import UUID

import numpy as np
import pytest

from app.services import pump_flow_history as fh
from app.services import pump_ml_candles as pc
from app.services import pump_ml_daily as daily
from app.services import pump_opportunity_engine as eng
from tests.test_pump_ml_candles import CFG, STEP, synthetic
from tests.test_pump_ml_feature_groups import bars_for

TF = CFG["taker_flow"]


def _snapshots(frame, cfg, *, extra_syms=(), lag=10.0, seed=3, skip=None):
    """Snapshot rows for every decision of ``frame`` (decision = grid + step)."""
    rng = np.random.default_rng(seed)
    decisions = [int(t) + STEP for t in frame["grid"]]
    rows = []
    for d in decisions:
        for s in list(frame["syms"]) + list(extra_syms):
            for w in cfg["taker_flow"]["windows_minutes"]:
                if skip and skip(s, d, w):
                    continue
                b, sl = float(rng.uniform(0, 100)), float(rng.uniform(0, 100))
                rows.append((s, d, w, b, sl, w, lag))
    flow = pc.FlowSnapshots(decisions, cfg["taker_flow"]["windows_minutes"], cfg["taker_flow"])
    flow.ingest(rows)
    return flow, rows


def test_imbalance_rule_matches_window_delta_norm_and_availability():
    v, r = pc.flow_imbalance(30.0, 10.0, 5, 12.0, 5, TF)
    assert v == pytest.approx(0.5) and r is None                       # (B-S)/(B+S)
    assert pc.flow_imbalance(30, 10, 3, 12, 5, TF) == (None, "flow_insufficient_coverage")   # 60 % < 80 %
    assert pc.flow_imbalance(30, 10, 4, 12, 5, TF)[0] == pytest.approx(0.5)                  # 80 % is enough
    assert pc.flow_imbalance(0, 0, 5, 12, 5, TF) == (None, "flow_no_volume")
    assert pc.flow_imbalance(None, 1, 5, 12, 5, TF) == (None, "flow_no_volume")
    assert pc.flow_imbalance(30, 10, 5, TF["max_lag_seconds"] + 1, 5, TF) == (None, "flow_snapshot_late")
    assert pc.flow_imbalance(30, 10, 5, None, 5, TF) == (None, "flow_snapshot_late")


def test_snapshot_sql_is_immutable_and_written_once_the_window_is_settled():
    assert "ON CONFLICT (symbol, decision_at, window_minutes) DO NOTHING" in fh.ASOF_SQL
    assert "UPDATE" not in fh.ASOF_SQL and "FILTER (WHERE NOT b.partial)" in fh.ASOF_SQL
    d = 1_790_000_100 - (1_790_000_100 % 300)
    last_minute = lambda now_ms: ((now_ms - 3_000) // 60_000) * 60_000 - 60_000   # service rule, settle 3 s
    assert fh.asof_decision(d * 1000 + 2_000, last_minute(d * 1000 + 2_000), 300) is None   # minute not settled
    assert fh.asof_decision(d * 1000 + 4_000, last_minute(d * 1000 + 4_000), 300) == d
    assert fh.asof_decision(d * 1000 + 4_000, None, 300) is None
    calls = []

    class Res:
        rowcount = 4

    class DB:
        async def execute(self, stmt, params):
            calls.append((str(stmt), params))
            return Res()

    out = asyncio.run(fh.write(DB(), ["B_USDT", "A_USDT"], {}, d * 1000 + 4_000,
                               {"step_seconds": 300, "asof_windows_minutes": [5, 15]}, "5m",
                               last_minute_ms=last_minute(d * 1000 + 4_000)))
    sql, params = [c for c in calls if "pump_flow_asof" in c[0]][0]
    assert out["asof"] == 4 and params["w"] == [5, 15] and params["s"] == ["A_USDT", "B_USDT"]
    assert params["d"] == datetime.fromtimestamp(d, timezone.utc)


def test_flow_columns_are_point_in_time_and_relative_uses_the_snapshot_universe():
    closes = synthetic(n=500)
    frame0 = pc.build_frame(closes, CFG)
    flow, rows = _snapshots(frame0, CFG, extra_syms=("GONE_USDT",))
    frame = pc.build_frame(closes, CFG, flow=flow, flow_all=True)
    col = 400
    d = int(frame["grid"][col]) + STEP
    at = {(s, w): (b, sl) for s, dd, w, b, sl, *_ in rows if dd == d}
    i = 5; sym = frame["syms"][i]
    b, sl = at[(sym, 5)]
    assert frame["features"]["tf_imb_5"][i, col] == pytest.approx((b - sl) / (b + sl))
    universe = [(bb - ss) / (bb + ss) for (s, w), (bb, ss) in at.items() if w == 5]   # includes GONE_USDT
    assert frame["features"]["tf_imb_rel_5"][i, col] == pytest.approx((b - sl) / (b + sl) - np.median(universe))
    assert frame["features"]["tf_missing_5"][i, col] == 0.0
    # a later snapshot (or a later revision of anything) never changes an earlier column
    later_rows = [(s, dd, w, (bb * 7 if dd > d else bb), ss, u, lg) for s, dd, w, bb, ss, u, lg in rows]
    flow2 = pc.FlowSnapshots(flow.decisions, flow.windows, TF); flow2.ingest(later_rows)
    frame2 = pc.build_frame(closes, CFG, flow=flow2, flow_all=True)
    for name in pc.flow_catalog(CFG):
        np.testing.assert_allclose(frame["features"][name][:, col], frame2["features"][name][:, col],
                                   equal_nan=True, err_msg=name)


def test_late_or_absent_snapshots_are_unavailable_and_flagged():
    closes = synthetic(n=500)
    frame0 = pc.build_frame(closes, CFG)
    late, _ = _snapshots(frame0, CFG, lag=TF["max_lag_seconds"] + 5)
    f = pc.build_frame(closes, CFG, flow=late, flow_all=True)["features"]
    assert np.all(np.isnan(f["tf_imb_5"])) and np.all(f["tf_missing_5"] == 1.0)
    target = frame0["syms"][2]
    holes, _ = _snapshots(frame0, CFG, skip=lambda s, d, w: s == target)
    f2 = pc.build_frame(closes, CFG, flow=holes, flow_all=True)["features"]
    assert np.all(np.isnan(f2["tf_imb_5"][2])) and np.isfinite(f2["tf_imb_5"][3, 300])
    none = pc.build_frame(closes, CFG, flow=None, flow_all=True)["features"]
    assert np.all(np.isnan(none["tf_imb_rel_15"])) and np.all(none["tf_missing_15"] == 1.0)


def test_relative_needs_min_assets_with_values():
    closes = synthetic(n=500)
    frame0 = pc.build_frame(closes, CFG)
    few, _ = _snapshots(frame0, CFG, skip=lambda s, d, w: s not in frame0["syms"][:3])
    f = pc.build_frame(closes, CFG, flow=few, flow_all=True)["features"]
    assert np.isfinite(f["tf_imb_5"][0, 300]) and np.isnan(f["tf_imb_rel_5"][0, 300])   # 3 < min_assets


def test_default_model_columns_are_unchanged_and_adopted_flow_is_appended_last():
    assert pc.flow_features(CFG) == [] and not pc.needs_flow(CFG)
    assert not any(n.startswith("tf_") for n in pc.feature_names(CFG))
    cfg = {**CFG, "taker_flow": {**TF, "features": ["tf_imb_rel_5"]}}
    assert pc.feature_names(cfg) == pc.feature_names(CFG) + ["tf_imb_rel_5"]


def test_live_cells_equal_training_values_with_flow():
    closes = synthetic(n=600); bars = bars_for(closes)
    cfg = {**CFG, "taker_flow": {**TF, "features": ["tf_imb_5", "tf_imb_rel_15"]}}
    flow, _ = _snapshots(pc.build_frame(closes, cfg), cfg)
    frame = pc.build_frame(closes, cfg, bars=bars, flow=flow)
    col = 480
    cells = pc.live_cells(frame, cfg, int(frame["grid"][col]) + STEP + 60)
    for name in ("tf_imb_5", "tf_imb_rel_15"):
        v = frame["features"][name][4, col]
        got = cells[frame["syms"][4]][name]["value"]
        assert np.isfinite(v) and got == pytest.approx(v)


def test_live_waits_for_a_pending_snapshot_instead_of_predicting_without_it():
    cfg = {**CFG, "taker_flow": {**TF, "features": ["tf_imb_5"]}}
    now_s = 1_790_000_100 - (1_790_000_100 % STEP)

    class Res:
        def __init__(self, rows): self._rows = rows
        def all(self): return self._rows

    class DB:
        def __init__(self, rows): self.rows = rows
        async def execute(self, stmt, params):
            return Res(self.rows if "pump_flow_asof" in str(stmt) else [])

    assert asyncio.run(pc.live_flow(DB([]), cfg, now_s, wall_s=now_s + 20)) == "pending"
    assert asyncio.run(pc.candle_context(DB([]), ["A_USDT"], cfg, now_s, wall_s=now_s + 20)) == {}
    gone = asyncio.run(pc.live_flow(DB([]), cfg, now_s, wall_s=now_s + TF["max_lag_seconds"] + 1))
    assert isinstance(gone, pc.FlowSnapshots)                     # truly missing → NaN, as in training
    rows = [("A_USDT", now_s, 5, 3.0, 1.0, 5, 8.0)]
    got = asyncio.run(pc.live_flow(DB(rows), cfg, now_s, wall_s=now_s + 9))
    assert isinstance(got, pc.FlowSnapshots) and got.syms == ["A_USDT"]


def test_adopted_flow_columns_are_context_features_in_the_manifest(tmp_path):
    closes = synthetic(n=1400, signal=0.5); bars = bars_for(closes)
    cfg = {**CFG, "taker_flow": {**TF, "features": ["tf_imb_5"]}, "max_rows": 6000}
    flow, _ = _snapshots(pc.build_frame(closes, cfg), cfg)
    frame = pc.build_frame(closes, cfg, horizons_candles=[3], bars=bars, flow=flow)
    rows = pc.training_rows(frame, cfg, 3, cfg["label_mode"])
    research = eng.config(None)["research"]
    out = pc.train_candle_model(rows, cfg=cfg, horizon_minutes=15, options={
        k: research[k] for k in ("calibration_C", "calibration_max_iter", "calibration_method",
                                 "calibration_max_slope", "bootstrap_repetitions")},
        params=research["params"], output_root=tmp_path)
    spec = out["manifest"]["spec"]
    assert spec["context_features"] == ["tf_imb_5"] and "tf_imb_5" not in spec["features"]
    assert spec["features"] + spec["context_features"] == pc.feature_names(cfg)   # training column order


def _day_flow(days_ok, days_bad_cov=(), start=date(2026, 10, 9)):
    """FlowSnapshots over consecutive days; every decision on time, 10 symbols."""
    total = len(days_ok) + len(days_bad_cov)
    t0 = int(datetime.combine(start, datetime.min.time(), timezone.utc).timestamp())
    decisions = list(range(t0, t0 + total * 86400, STEP))
    flow = pc.FlowSnapshots(decisions, TF["windows_minutes"], TF)
    rows = []
    for d in decisions:
        day = datetime.fromtimestamp(d, timezone.utc).date()
        bad = (day - start).days in days_bad_cov
        for i in range(10):
            for w in TF["windows_minutes"]:
                usable = 0 if (bad and i < 5) else w
                rows.append((f"S{i}_USDT", d, w, 2.0, 1.0, usable, 5.0))
    flow.ingest(rows)
    return flow, start


def test_readiness_counts_usable_days_and_never_releases_by_date():
    n = 2 + TF["readiness"]["min_test_days"]
    flow, start = _day_flow(range(n - 2), days_bad_cov=(n - 2, n - 1))
    today = start + timedelta(days=n)
    r = pc.flow_readiness(flow, CFG, 2, today)
    assert r["required_days"] == n and r["usable_days"] == n - 2 and not r["ready"]
    assert r["days"][-1]["reasons"] == ["row_coverage_below_min"]          # 50 % < 80 %
    assert r["review_estimate"]["date"] == (today + timedelta(days=2)).isoformat()
    assert "not a release" in r["review_estimate"]["assumption"]
    flow2, start2 = _day_flow(range(n))
    r2 = pc.flow_readiness(flow2, CFG, 2, start2 + timedelta(days=n))
    assert r2["ready"] and "review_estimate" not in r2
    r3 = pc.flow_readiness(flow2, CFG, 2, start2 + timedelta(days=n - 1))   # last day still running
    assert not r3["ready"] and r3["days"][-1]["reasons"] == ["incomplete_day"]


def _wf(aucs, briers):
    return {"folds": [{"day": f"2026-11-{i + 1:02d}", "auc": a, "brier": b} for i, (a, b) in enumerate(zip(aucs, briers))],
            "pooled": {"brier_improvement_day_mean": 0.003}}


def test_preregistered_test_requires_sign_blocks_calibration_and_days():
    crit = TF["criterion"]
    base = _wf([0.55] * 24, [0.20] * 24)
    good = _wf([0.56 + 0.001 * (i % 3) for i in range(24)], [0.20] * 24)
    t = pc.preregistered_test(base, good, crit, 4, 0.003, 20)
    assert t["passes"] and t["level"] == pytest.approx(0.0125) and t["days_better"] == 24
    assert t["block_bootstrap"]["auc_delta_lower"] > 0
    assert pc.preregistered_test(base, good, crit, 4, 0.003, 20) == t      # seeded: reproducible
    worse_cal = _wf([0.56] * 24, [0.2005] * 24)                             # +0.0005 > 0.1 × 0.003
    t2 = pc.preregistered_test(base, worse_cal, crit, 4, 0.003, 20)
    assert not t2["passes"] and t2["failed"] == ["calibration_worse"]
    tied = _wf([0.55] * 20 + [0.56] * 4, [0.20] * 24)
    t3 = pc.preregistered_test(base, tied, crit, 4, 0.003, 20)
    assert t3["ties"] == 20 and "sign_test" in t3["failed"]                 # 4/4 better: p 0.0625
    short = _wf([0.56] * 10, [0.20] * 10)
    assert "insufficient_paired_days" in pc.preregistered_test(base, short, crit, 4, 0.003, 20)["failed"]
    mixed = _wf([0.56 if i % 2 else 0.54 for i in range(24)], [0.20] * 24)
    t4 = pc.preregistered_test(base, mixed, crit, 4, 0.003, 20)
    assert set(t4["failed"]) >= {"sign_test", "block_bootstrap_auc"}


def test_taker_flow_config_is_validated():
    cd = eng.config(None)["research"]["candle"]
    tf = cd["taker_flow"]
    assert tf["features"] == [] and set(tf["ablation"]["variants"]) == {"raw_5", "raw_15", "rel_5", "rel_15"}
    bad_cases = [{"features": ["tf_imb_30"]}, {"features": ["tf_imb_5", "tf_imb_5"]},
                 {"windows_minutes": [7]}, {"max_lag_seconds": 0},
                 {"criterion": {**tf["criterion"], "alpha": 0.5}},
                 {"criterion": {**tf["criterion"], "direction": "two_sided"}},
                 {"readiness": {**tf["readiness"], "min_test_days": 2}},
                 {"ablation": {**tf["ablation"], "variants": {"x": ["rsi"]}}}]
    for bad in bad_cases:
        with pytest.raises(ValueError):
            eng.config({"research": {"candle": {**cd, "taker_flow": {**tf, **bad}}}})
    eng.config({"research": {"candle": {**cd, "taker_flow": {**tf, "features": ["tf_imb_rel_5"]}}}})
    with pytest.raises(ValueError):          # stored overrides merge into the defaults, so test directly
        eng._validate_taker_flow({**cd, "taker_flow": {**tf, "ablation": {**tf["ablation"], "variants": {}}}})


def _runner_env(monkeypatch, ready_days, wf_calls):
    from app.services import pump_directional_research as dr
    closes = synthetic(n=900); bars = bars_for(closes)
    first = int(min(min(v) for v in closes.values()))
    start = datetime.fromtimestamp(first, timezone.utc).date() + timedelta(days=1)

    async def fake_bars(conn, symbols, tf, lo, hi):
        return closes, bars

    async def fake_flow(conn, cfg, lo, hi):
        frame = pc.build_frame(closes, cfg)
        flow, _ = _snapshots(frame, cfg)
        return flow

    def fake_ready(flow, cfg, min_train_days, today):
        days = [start + timedelta(days=i) for i in range(ready_days)]
        req = min_train_days + cfg["taker_flow"]["readiness"]["min_test_days"]
        return {"ready": ready_days >= req, "usable_days": ready_days, "required_days": req, "days": [],
                "usable_day_list": [d.isoformat() for d in days]}

    def fake_wf(rows, *, columns, spec, options, relative):
        wf_calls.append(list(columns))
        bump = 0.01 if any(c.startswith("tf_imb") for c in columns) else 0.0
        return {"scored_days": 24, "folds": [{"day": f"d{i:02d}", "auc": 0.55 + bump + 0.001 * (i % 2),
                                              "brier": 0.2} for i in range(24)],
                "pooled": {"day_auc_median": 0.55 + bump, "brier_improvement_day_mean": 0.003}}

    class Conn:
        async def fetchrow(self, sql, *args):
            return {"lo": datetime.fromtimestamp(first, timezone.utc), "hi": None}
        async def fetch(self, sql, *args):
            return [{"symbol": s} for s in closes]

    monkeypatch.setattr(pc, "load_bars", fake_bars)
    monkeypatch.setattr(pc, "load_flow", fake_flow)
    monkeypatch.setattr(pc, "flow_readiness", fake_ready)
    monkeypatch.setattr(dr, "walk_forward_evaluation", fake_wf)
    c = eng.config(None)
    c["research"]["candle"]["taker_flow"]["ablation"]["max_rows"] = 2000
    return Conn(), c


def test_runner_refuses_until_usable_history_is_enough(monkeypatch):
    wf_calls = []
    conn, c = _runner_env(monkeypatch, 5, wf_calls)
    diag = {}
    with pytest.raises(ValueError, match="insufficient_usable_flow_history"):
        asyncio.run(pc.run_taker_flow_ablation(conn, UUID(int=1), c, diag,
                                               datetime.now(timezone.utc) + timedelta(hours=1)))
    assert wf_calls == [] and diag["taker_flow"]["readiness"]["usable_days"] == 5
    assert diag["taker_flow"]["preregistration_hash"]


def test_runner_declares_the_family_and_tests_every_candidate_against_the_same_base(monkeypatch):
    wf_calls, saved = [], []
    conn, c = _runner_env(monkeypatch, 3, wf_calls)
    c["research"]["candle"]["taker_flow"]["readiness"]["min_test_days"] = 1      # gate passes on 3 days

    async def progress(partial):
        saved.append(json.loads(json.dumps(partial, default=str)))

    out = asyncio.run(pc.run_taker_flow_ablation(conn, UUID(int=1), c, {},
                                                 datetime.now(timezone.utc) + timedelta(hours=1), progress=progress))
    assert list(out["variants"]) == ["base", "raw_5", "raw_15", "rel_5", "rel_15", "missing_5"]
    base_cols = out["preregistration"]["base_columns"]
    assert wf_calls[0] == base_cols and all(cols[:len(base_cols)] == base_cols for cols in wf_calls)
    assert wf_calls[1] == base_cols + ["tf_imb_5"] and wf_calls[-1] == base_cols + ["tf_missing_5"]
    assert out["preregistration"]["k"] == 4 and out["variants"]["raw_5"]["test"]["k"] == 4
    assert out["variants"]["missing_5"]["role"] == "control" and "interpretation" in out["variants"]["missing_5"]["test"]
    assert "missing_5" not in out["candidates_passing"] and out["activation"] == "manual_only_after_review"
    assert set(out["missing_share"]) == {"tf_imb_5", "tf_imb_15", "tf_imb_rel_5", "tf_imb_rel_15"}
    assert len(saved) == 1 + 6
    days = {r for r in out["readiness"]["usable_day_list"]}
    assert out["days"] <= len(days)                                   # rows only from usable days


def test_family_is_registered_and_a_refusal_is_recorded_as_blocked(monkeypatch):
    assert "taker_flow_ablation" in daily.FAMILIES
    calls = []

    class Conn:
        async def fetchval(self, sql, *args):
            if "pg_try_advisory_lock" in sql: return True
            if "SELECT run_id" in sql: return None
            if "config_json" in sql: return {"enabled": True, "training_job_enabled": True}
            return 0
        async def execute(self, sql, *args): calls.append((sql, args))
        def transaction(self): raise AssertionError("no model is saved")

    async def refuse(conn, owner, c, diag, deadline, progress=None):
        diag["taker_flow"] = {"readiness": {"ready": False, "usable_days": 1}}
        raise ValueError("insufficient_usable_flow_history")

    monkeypatch.setattr(pc, "run_taker_flow_ablation", refuse)
    out = asyncio.run(daily.run_owner(Conn(), UUID(int=1), horizons=[15], force=True, family="taker_flow_ablation"))
    assert out["status"] == "blocked" and out["reason"] == "insufficient_usable_flow_history"
    assert out["selection"]["taker_flow"]["readiness"]["usable_days"] == 1 and out["applied_delta"] == 0
