"""AUD-PUMP-ML-001 read-only live audit: pure helpers + end-to-end on local PostgreSQL."""
import json
import os
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import numpy as np
import pytest

from scripts import audit_pump_ml_live as audit

URL = os.environ.get("SHADOW_L3_TEST_DATABASE_URL")


def test_slot_staleness_uses_the_newest_candle_ingested_by_the_row_time():
    step, d = 300, 10_000_000
    first = {d - 300: d + 20, d - 600: d - 280, d - 900: d - 580}
    assert audit.slot_staleness(d, d + 30, first, step) == 0        # decision candle already there
    assert audit.slot_staleness(d, d + 10, first, step) == 1        # not yet: frame ends one candle earlier
    assert audit.slot_staleness(d, d - 600, first, step, max_back=2) is None


def test_day_auc_matches_per_day_statistic_and_sign_test():
    rng = np.random.default_rng(1)
    Y = rng.integers(0, 2, 400)
    P = np.where(Y == 1, 0.6, 0.4) + rng.normal(0, 0.3, 400)
    D = np.repeat(["2026-01-01", "2026-01-02", "2026-01-03", "2026-01-04"], 100)
    out = audit.day_auc(P, Y, D)
    assert out["days"] == 4 and out["days_above_half"] == 4 and out["sign_test_p"] == 1 / 16
    assert out["day_auc_median"] > 0.6


def test_economics_reports_top_minus_bottom_and_top_minus_all():
    P = np.tile(np.linspace(0, 1, 100), 3)
    E = (P - 0.5) * 2                            # excess grows with p
    D = np.repeat(["a", "b", "c"], 100)
    out = audit.economics(P, E, D, 0.1, 200, 7)
    assert out["top_minus_bottom"] > 1.5 and 0.7 < out["top_minus_all"] < 1.0
    assert out["days_top_minus_bottom_positive"] == 3 and out["top_rows"] == out["bottom_rows"] == 30


def test_effect_profile_uses_configured_bounds():
    ml = {"score": {"max_adjust": 0.05}, "direction": {"confirm_up": 0.6, "confirm_down": 0.4}}
    out = audit.effect_profile([0.5, 0.55, 0.61, 0.3], ml)
    assert out["share_p_ge_confirm_up"] == 0.25 and out["share_p_le_confirm_down"] == 0.25
    assert out["score_multiplier"]["max"] == pytest.approx(1.011, abs=1e-3)


# ── end to end ────────────────────────────────────────────────────────────────
def _closes(n_sym=12, days=4, step=300, seed=3):
    rng = np.random.default_rng(seed)
    start = int(datetime(2026, 3, 1, tzinfo=timezone.utc).timestamp())
    T = days * 86400 // step
    syms = [f"S{i:02d}_USDT" for i in range(n_sym)] + ["BTC_USDT"]
    mkt = np.cumsum(rng.normal(0, 0.1, T))
    out = {}
    for j, s in enumerate(syms):
        resid = np.zeros(T)
        for t in range(1, T):                     # mean-reverting residual: learnable
            resid[t] = 0.5 * resid[t - 1] + rng.normal(0, 0.3)
        logp = mkt * (0.8 + 0.05 * j) + resid
        out[s] = {start + t * step: float(100 * np.exp(logp[t] / 100)) for t in range(T)}
    return out, start, T


@pytest.mark.skipif(not URL, reason="Local PostgreSQL URL required")
@pytest.mark.asyncio
async def test_end_to_end_replay_reconstructs_live_and_detects_stale_slots(tmp_path, monkeypatch):
    from sqlalchemy import create_engine, text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from app.services import pump_ml_candles as pc
    from app.services import pump_ml_inference as inf
    from app.services import pump_monitor_service as svc
    from app.services import pump_opportunity_engine as eng
    from app.services import pump_score_v1 as v1

    research = deepcopy(eng.config(None)["research"])
    cfg = {**research["candle"], "walk_forward": research["walk_forward"], "lookback_days": 4}
    closes, start, T = _closes()
    step = 300
    bars = {s: {t: (c, c * 1.001, c * 0.999, 1000.0) for t, c in v.items()} for s, v in closes.items()}
    cut = start + 3 * 86400                                    # train on 3 days, "live" on the 4th
    train_c = {s: {t: c for t, c in v.items() if t < cut} for s, v in closes.items()}
    train_b = {s: {t: b for t, b in v.items() if t < cut} for s, v in bars.items()}
    frame = pc.build_frame(train_c, cfg, horizons_candles=[3], bars=train_b)
    rows = pc.training_rows(frame, cfg, 3, cfg["label_mode"])
    result = pc.train_candle_model(rows, cfg=cfg, horizon_minutes=15, options={
        k: research[k] for k in ("calibration_C", "calibration_max_iter", "calibration_method",
                                 "calibration_max_slope", "bootstrap_repetitions")},
        params=research["params"], output_root=tmp_path)
    man = result["manifest"]; exp = str(uuid4())
    folder = Path(tmp_path) / man["artifact_namespace"]
    files = {n: (folder / n).read_bytes() for n in ("xgboost.json", "calibrator.json")}
    model = inf.DirectionalModel(exp, man, result["metrics"], files["xgboost.json"],
                                 json.loads(files["calibrator.json"]), "x")

    # live: every slot of the last day; odd slots logged before the decision candle arrived
    full = pc.build_frame(closes, man["spec"]["candle"], bars=bars)
    col = {int(t): j for j, t in enumerate(full["grid"])}
    uid = uuid4(); preds, candles = [], []
    stale_slots = set()
    for n, d in enumerate(range(cut + step, start + T * step - 6 * step, step)):
        stale = n % 2 == 1
        created = d + 5
        if stale:
            stale_slots.add(d)
        t = col[d - step] - (1 if stale else 0)
        for i, s in enumerate(full["syms"]):
            cells = {f: {"value": float(full["features"][f][i, t]) if np.isfinite(full["features"][f][i, t]) else None,
                         "reason": None if np.isfinite(full["features"][f][i, t]) else "nan"} for f in model.features}
            v = model.vector(cells, float("inf"))
            if v is not None:
                preds.append((exp, datetime.fromtimestamp(d, timezone.utc), s, model.predict([v])[0],
                              datetime.fromtimestamp(created, timezone.utc)))
    for s, v in closes.items():
        for t, c in v.items():
            close_at = t + step
            ing = close_at + (60 if close_at in stale_slots else 2)   # stale slot: decision candle lands late
            candles.append((t, s, c, ing))

    schema = "pump_audit_" + uuid4().hex
    sync = create_engine(URL)
    with sync.begin() as c:
        c.execute(text(f'CREATE SCHEMA "{schema}"'))
        c.execute(text(f'SET search_path TO "{schema}"'))
        c.execute(text("CREATE TABLE ohlcv(time timestamptz, symbol text, exchange text, market_type text, timeframe text,"
                       " open numeric, high numeric, low numeric, close numeric, volume numeric, quote_volume numeric,"
                       " is_closed boolean, ingested_at timestamptz)"))
        c.execute(text("CREATE TABLE pump_ml_live_predictions(user_id uuid, experiment_id text, objective varchar(48),"
                       " horizon_minutes int, decision_at timestamptz, symbol varchar(40), p_up float8, applied bool,"
                       " created_at timestamptz)"))
        c.execute(text("CREATE TABLE pump_ml_experiments(experiment_id uuid, created_at timestamptz, manifest jsonb, metrics jsonb)"))
        c.execute(text("CREATE TABLE pump_ml_artifacts(experiment_id uuid, path text, content bytea)"))
        c.execute(text("CREATE TABLE config_profiles(user_id uuid, config_type text, is_active bool, pool_id uuid,"
                       " config_json jsonb, updated_at timestamptz)"))
        c.execute(text("INSERT INTO ohlcv SELECT to_timestamp(t), s, 'gate.io', 'spot', '5m', c, c*1.001, c*0.999, c, 1000, 1000,"
                       " true, to_timestamp(i) FROM unnest(CAST(:t AS float8[]), CAST(:s AS text[]), CAST(:c AS float8[]),"
                       " CAST(:i AS float8[])) AS x(t, s, c, i)"),
                  {"t": [x[0] for x in candles], "s": [x[1] for x in candles], "c": [x[2] for x in candles],
                   "i": [x[3] for x in candles]})
        c.execute(text("INSERT INTO pump_ml_live_predictions SELECT :u, e, :o, 15, d, s, p, true, ca FROM unnest("
                       "CAST(:e AS text[]), CAST(:d AS timestamptz[]), CAST(:s AS text[]), CAST(:p AS float8[]),"
                       " CAST(:ca AS timestamptz[])) AS x(e, d, s, p, ca)"),
                  {"u": uid, "o": inf.CANDLE_OBJECTIVE, "e": [p[0] for p in preds], "d": [p[1] for p in preds],
                   "s": [p[2] for p in preds], "p": [p[3] for p in preds], "ca": [p[4] for p in preds]})
        c.execute(text("INSERT INTO pump_ml_experiments VALUES (:e, now(), CAST(:m AS jsonb), CAST(:x AS jsonb))"),
                  {"e": exp, "m": json.dumps(man), "x": json.dumps(result["metrics"], default=str)})
        for n, b in files.items():
            c.execute(text("INSERT INTO pump_ml_artifacts VALUES (:e, :p, :b)"), {"e": exp, "p": f"x/{n}", "b": b})
        c.execute(text("INSERT INTO config_profiles VALUES (:u, 'ml', true, NULL, CAST(:j AS jsonb), now())"),
                  {"u": uid, "j": json.dumps({"ml_fee_roundtrip_pct": 0.2})})

    spec = deepcopy(v1.DEFAULT_V1); spec["ml"]["objective"] = "relative_candle"; spec["ml"]["horizon_minutes"] = 15

    async def fake_config(db, user_id):
        return {"score_v1": spec}
    monkeypatch.setattr(svc, "get_config", fake_config)
    engine = create_async_engine(URL.replace("postgresql://", "postgresql+asyncpg://"),
                                 connect_args={"server_settings": {"search_path": schema}})
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            args = type("A", (), {"days": 4000, "max_back": 4, "symbol_chunk": 5, "user_id": None, "top_features": 40})
            rep = await audit.execute(db, args)
    finally:
        await engine.dispose()
        with sync.begin() as c:
            c.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        sync.dispose()

    assert rep["status"] == "ok"
    a = rep["A_freshness"]
    assert set(a["slot_classes"]) == {"FRESH", "STALE_1"}
    assert abs(a["slot_classes"]["FRESH"] - a["slot_classes"]["STALE_1"]) <= 1
    g = rep["B_replay"]["groups"]
    assert g["all"]["corr_live_vs_replay_seen"]["mean_abs_diff"] < 1e-6      # reconstruction is exact
    assert g["fresh_slots"]["corr_live_vs_replay_intended"]["mean_abs_diff"] < 1e-6
    assert g["stale_slots"]["corr_live_vs_replay_intended"]["mean_abs_diff"] > 1e-3
    assert rep["config_ml_fee"] == {"ml_fee_roundtrip_pct": 0.2}
    assert rep["D_economics"]["live"]["rows"] == rep["B_replay"]["mature_rows"]
    # synthetic residual is mean-reverting: the model must reward the asset that just lagged
    assert rep["F_feature_direction"]["rel_resid_1"]["spearman_vs_p_live"] < 0
