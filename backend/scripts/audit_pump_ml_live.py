"""AUD-PUMP-ML-001 -- read-only audit of the APPLIED Pump ML candle model, live.

The Monitor shows walk-forward AUC ~0.56 and a live AUC ~0.51 for the same
objective. This script tells apart the possible causes from rows, never from
the stored metrics:

  A. Freshness. A prediction row is written by the FIRST monitor cycle of a
     candle slot (ON CONFLICT DO NOTHING) and the candle cells are cached for
     the whole slot. ``live_cells`` silently falls back to the newest candle in
     the frame, and candle cells carry no ``age_seconds``, so the
     ``max_feature_age_seconds`` guard never fires. For each slot we check,
     with ``ohlcv.ingested_at``, which candle was the newest one ingested when
     the row was written (``created_at``): FRESH = the decision candle
     (open = decision - step); STALE_n = n candles older.
  B. Replay. The SAME experiment artifact scored offline on today's complete
     candles at the intended column (decision candle) and at the column the
     live cycle could see. ``p_replay_seen ~= p_live`` validates the
     reconstruction; AUC(replay_intended) - AUC(live) on the same rows is what
     the live pipeline costs.
  C. Alignment control: AUC of the live p against the label shifted -1/0/+1.
  D. Economics: decile excess return (pp, beta-residual vs universe median,
     mid close-to-close, BEFORE costs) top-bottom and top-all, CI by
     resampling days; compared with ``ml_fee_roundtrip_pct`` [config: ml].
  E. Effect size: distribution of p, share past the direction thresholds and
     the score multiplier actually applied.
  F. What the model rewards: Spearman of the top-gain features vs the live p
     and vs the realised label (e.g. a short-term reversal model penalises the
     asset that just outperformed).

Strictly read-only (session READ ONLY). Same universe as ``live_evaluation``
(symbols with a logged prediction + the reference).

  python -m scripts.audit_pump_ml_live --days 14 --output pump_ml_live_audit.json

The decile and bootstrap count come from the Pump research config (as live_evaluation).
"""
import argparse
import asyncio
import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

CANDLES_SQL = """
    SELECT DISTINCT ON (symbol, time) symbol, time, open::float8, high::float8, low::float8, close::float8,
           coalesce(quote_volume, volume)::float8 AS v, ingested_at
      FROM ohlcv
     WHERE symbol = ANY(CAST(:s AS text[])) AND timeframe = :tf AND market_type = 'spot'
       AND is_closed IS TRUE AND time >= :lo AND time < :hi
     ORDER BY symbol, time, CASE WHEN exchange ILIKE 'gate%' THEN 0 ELSE 1 END
"""

PREDS_SQL = """
    SELECT experiment_id, decision_at, symbol, p_up, applied, created_at FROM pump_ml_live_predictions
     WHERE user_id = CAST(:u AS uuid) AND objective = :o AND horizon_minutes = :h AND decision_at >= :since
     ORDER BY decision_at
"""


# ── pure helpers (unit-tested) ────────────────────────────────────────────────
def quantiles(values, qs=(0.5, 0.9, 0.99)):
    import numpy as np
    a = np.asarray([v for v in values if v is not None and math.isfinite(v)], dtype=float)
    if not a.size:
        return None
    return {"n": int(a.size), **{f"p{int(q * 100)}": round(float(np.quantile(a, q)), 3) for q in qs},
            "max": round(float(a.max()), 3)}


def slot_staleness(decision_s, created_s, first_ingested, step, max_back=4):
    """Candles between the decision candle and the newest candle ingested (by any
    symbol) at ``created_s``: 0 = fresh; None = nothing in ``max_back`` candles."""
    for n in range(max_back + 1):
        ing = first_ingested.get(decision_s - step * (n + 1))
        if ing is not None and ing <= created_s:
            return n
    return None


def sign_test_p(successes, n):
    from math import comb
    return None if n <= 0 else sum(comb(n, i) for i in range(successes, n + 1)) / 2 ** n


def day_auc(P, Y, D, min_rows=20):
    """Same statistic as ``pump_ml_live.evaluate``: per-UTC-day AUC, median, sign test."""
    import numpy as np
    from sklearn.metrics import roc_auc_score
    P, Y, D = np.asarray(P, float), np.asarray(Y, int), np.asarray(D, object)
    ok = np.isfinite(P)
    P, Y, D = P[ok], Y[ok], D[ok]
    if len(P) < 2 or len(set(Y.tolist())) < 2:
        return {"rows": int(len(P)), "days": 0}
    aucs = []
    for d in sorted(set(D.tolist())):
        m = D == d
        if m.sum() >= min_rows and len(set(Y[m].tolist())) == 2:
            aucs.append(float(roc_auc_score(Y[m], P[m])))
    a = np.array(aucs)
    above = int((a > 0.5).sum())
    return {"rows": int(len(P)), "days": int(a.size), "day_auc_median": float(np.median(a)) if a.size else None,
            "days_above_half": above, "sign_test_p": sign_test_p(above, int(a.size)),
            "pooled_auc": float(roc_auc_score(Y, P))}


def economics(P, E, D, quantile, reps, seed):
    """Top/bottom ``quantile`` of p (thresholds over all rows, as live/walk-forward);
    pp excess; CI95 by resampling DAYS."""
    import numpy as np
    P, E, D = np.asarray(P, float), np.asarray(E, float), np.asarray(D, object)
    ok = np.isfinite(P) & np.isfinite(E)
    P, E, D = P[ok], E[ok], D[ok]
    if len(P) < 20:
        return None
    hi, lo = np.quantile(P, 1 - quantile), np.quantile(P, quantile)
    days = sorted(set(D.tolist()))
    S = []
    for d in days:
        m = D == d
        t, b, a = E[m & (P >= hi)], E[m & (P <= lo)], E[m]
        S.append((t.sum(), t.size, b.sum(), b.size, a.sum(), a.size))
    S = np.array(S, float)

    def stats(idx):
        s = S[idx].sum(axis=0)
        if not s[1] or not s[3] or not s[5]:
            return np.nan, np.nan
        return s[0] / s[1] - s[2] / s[3], s[0] / s[1] - s[4] / s[5]
    rng = np.random.default_rng(seed)
    boot = np.array([stats(rng.integers(0, len(S), len(S))) for _ in range(reps)])
    per_day = np.array([stats(np.array([i])) for i in range(len(S))])
    tot = S.sum(axis=0)
    tmb, tma = stats(np.arange(len(S)))

    def ci(col):
        v = boot[:, col][np.isfinite(boot[:, col])]
        return [round(float(x), 4) for x in np.quantile(v, [.025, .975])] if v.size else None
    return {"quantile": quantile, "unit": "pp (percent points), beta-residual vs universe median, mid close-to-close, "
            "before costs", "rows": int(len(P)), "top_rows": int(tot[1]), "bottom_rows": int(tot[3]),
            "top_mean": round(float(tot[0] / tot[1]), 4), "bottom_mean": round(float(tot[2] / tot[3]), 4),
            "all_mean": round(float(tot[4] / tot[5]), 4),
            "top_minus_bottom": round(float(tmb), 4), "top_minus_bottom_ci95": ci(0),
            "top_minus_all": round(float(tma), 4), "top_minus_all_ci95": ci(1),
            "days": len(days), "days_top_minus_bottom_positive": int((per_day[:, 0] > 0).sum()),
            "days_top_minus_all_positive": int((per_day[:, 1] > 0).sum()), "ci_scope": "bootstrap_over_days"}


def effect_profile(P, ml):
    import numpy as np
    P = np.asarray(P, float)
    P = P[np.isfinite(P)]
    if not P.size:
        return None
    k = float(ml["score"]["max_adjust"])
    up, down = float(ml["direction"]["confirm_up"]), float(ml["direction"]["confirm_down"])
    mult = np.clip(1 + k * (2 * P - 1), 1 - k, 1 + k)
    return {"rows": int(P.size), "p": quantiles(P.tolist(), (0.01, 0.1, 0.5, 0.9, 0.99)),
            "share_p_ge_confirm_up": round(float((P >= up).mean()), 5),
            "share_p_le_confirm_down": round(float((P <= down).mean()), 5),
            "score_multiplier": quantiles(mult.tolist(), (0.01, 0.5, 0.99)),
            "config": {"max_adjust": k, "confirm_up": up, "confirm_down": down}}


def corr(a, b):
    import numpy as np
    a, b = np.asarray(a, float), np.asarray(b, float)
    ok = np.isfinite(a) & np.isfinite(b)
    if ok.sum() < 3:
        return None
    return {"n": int(ok.sum()), "pearson": round(float(np.corrcoef(a[ok], b[ok])[0, 1]), 4),
            "mean_abs_diff": round(float(np.abs(a[ok] - b[ok]).mean()), 5)}


# ── I/O ───────────────────────────────────────────────────────────────────────
async def _owner(db, args, since):
    if args.user_id:
        return args.user_id
    users = (await db.execute(text("SELECT DISTINCT user_id::text FROM pump_ml_live_predictions WHERE decision_at >= :s"),
                              {"s": since})).scalars().all()
    if len(users) != 1:
        raise SystemExit(f"{len(users)} users with live predictions; pass --user-id")
    return users[0]


async def _experiments(db, ids):
    from app.services.pump_ml_inference import DirectionalModel
    rows = (await db.execute(text("""SELECT experiment_id::text AS e, created_at, manifest, metrics
        FROM pump_ml_experiments WHERE experiment_id::text = ANY(CAST(:ids AS text[]))"""), {"ids": ids})).mappings().all()
    files = defaultdict(dict)
    for r in (await db.execute(text("""SELECT experiment_id::text AS e, path, content FROM pump_ml_artifacts
        WHERE experiment_id::text = ANY(CAST(:ids AS text[]))"""), {"ids": ids})).mappings().all():
        files[r["e"]][r["path"].rsplit("/", 1)[-1]] = bytes(r["content"])
    out = {}
    for r in rows:
        man = r["manifest"] if isinstance(r["manifest"], dict) else json.loads(r["manifest"])
        met = r["metrics"] if isinstance(r["metrics"], dict) else json.loads(r["metrics"])
        f = files.get(r["e"], {})
        model = None
        if "xgboost.json" in f and "calibrator.json" in f:
            model = DirectionalModel(r["e"], man, met, f["xgboost.json"], json.loads(f["calibrator.json"]),
                                     r["created_at"].isoformat())
        out[r["e"]] = {"created_at": r["created_at"], "manifest": man, "metrics": met, "model": model}
    return out


async def _candles(db, symbols, tf, lo, hi, chunk):
    closes, bars, ingested = {}, {}, {}
    for i in range(0, len(symbols), chunk):
        part = symbols[i:i + chunk]
        for sym, t, o, h, l, c, v, ing in (await db.execute(text(CANDLES_SQL), {
                "s": part, "tf": tf, "lo": lo, "hi": hi})).all():
            if c is None:
                continue
            e = int(t.timestamp())
            closes.setdefault(sym, {})[e] = float(c)
            bars.setdefault(sym, {})[e] = (o, h, l, v)
            if ing is not None:
                ingested[(sym, e)] = ing.timestamp()
        print(f"[audit] candles {min(i + chunk, len(symbols))}/{len(symbols)} symbols", flush=True)
    return closes, bars, ingested


def _fee(ml_config):
    v = (ml_config or {}).get("ml_fee_roundtrip_pct")
    return {"ml_fee_roundtrip_pct": v} if v is not None else {"ml_fee_roundtrip_pct": "NAO_CONFIGURADO"}


async def execute(db, args):
    import numpy as np
    from app.services import pump_ml_inference as inf
    from app.services import pump_monitor_service as svc
    from app.services import pump_opportunity_engine as eng
    from app.services.pump_ml_candles import REFERENCE, build_frame, needs_bars, needs_flow

    await db.execute(text("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY"))
    await db.commit()
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=args.days)
    user_id = await _owner(db, args, since)
    config = await svc.get_config(db, user_id)
    spec = config["score_v1"]; ml = spec["ml"]
    objective, horizon = inf.objective_for(spec), int(ml["horizon_minutes"])
    ml_cfg = (await db.execute(text("""SELECT config_json FROM config_profiles WHERE config_type='ml'
        AND is_active AND pool_id IS NULL AND user_id = CAST(:u AS uuid) LIMIT 1"""), {"u": user_id})).scalar_one_or_none()
    report = {"audit": "AUD-PUMP-ML-001", "generated_at": now.isoformat(), "user_id": user_id, "days": args.days,
              "objective": objective, "horizon_minutes": horizon,
              "config_score_v1_ml": {k: ml.get(k) for k in ("enabled", "objective", "horizon_minutes",
                                                            "max_model_age_days", "max_feature_age_seconds",
                                                            "quality", "score", "direction", "live_log")},
              "config_ml_fee": _fee(ml_cfg if isinstance(ml_cfg, dict) else json.loads(ml_cfg) if ml_cfg else {})}
    preds = (await db.execute(text(PREDS_SQL), {"u": user_id, "o": objective, "h": horizon, "since": since})).all()
    print(f"[audit] predictions={len(preds)}", flush=True)
    if not preds:
        return {**report, "status": "no_predictions"}
    exps = await _experiments(db, sorted({p[0] for p in preds}))
    cfgs = {json.dumps((e["manifest"].get("spec") or {}).get("candle"), sort_keys=True) for e in exps.values()}
    report["experiments"] = {k: {"created_at": v["created_at"].isoformat(), "artifacts": v["model"] is not None,
                                 "rows": sum(1 for p in preds if p[0] == k)} for k, v in exps.items()}
    report["experiments_missing"] = sorted({p[0] for p in preds} - set(exps))
    if len(cfgs) != 1:
        return {**report, "status": "mixed_candle_params", "candle_params_variants": len(cfgs)}
    cfg = json.loads(cfgs.pop())
    if needs_flow(cfg):
        return {**report, "status": "flow_features_not_replayable_here"}
    step = int(cfg["step_seconds"])
    k = max(1, int(round(horizon * 60 / step)))
    mode = cfg.get("label_mode", "path_mean")
    syms = sorted({p[2] for p in preds} | {REFERENCE})
    first_d = preds[0][1]; last_d = preds[-1][1]
    lo = first_d - timedelta(seconds=step * (int(cfg["beta_window"]) + max(int(x) for x in cfg["prev_windows"]) + 8))
    hi = last_d + timedelta(minutes=horizon, seconds=2 * step)
    closes, bars, ingested = await _candles(db, syms, cfg["timeframe"], lo, hi, args.symbol_chunk)

    # A. freshness ------------------------------------------------------------------
    first_ing = {}
    for (s, e), ing in ingested.items():
        first_ing[e] = min(first_ing.get(e, ing), ing)
    lag = [ing - (e + step) for (s, e), ing in ingested.items() if since.timestamp() - step <= e]
    slots = defaultdict(list)
    for j, p in enumerate(preds):
        slots[(p[0], int(p[1].timestamp()))].append(j)
    slot_stale, row_stale, created_offset = {}, [None] * len(preds), []
    for (e, d), idx in slots.items():
        created = min(preds[j][5].timestamp() for j in idx)
        created_offset.append(created - d)
        n = slot_staleness(d, created, first_ing, step, args.max_back)
        slot_stale[(e, d)] = n
        for j in idx:
            row_stale[j] = n
    own_fresh = Counter()
    for p in preds:
        ing = ingested.get((p[2], int(p[1].timestamp()) - step))
        own_fresh["own_candle_ingested_before_row" if ing is not None and ing <= p[5].timestamp()
                  else "own_candle_missing" if ing is None else "own_candle_ingested_after_row"] += 1
    cls = Counter("FRESH" if n == 0 else f"STALE_{n}" if n is not None else f"STALE_>{args.max_back}"
                  for n in slot_stale.values())
    report["A_freshness"] = {
        "slots": len(slot_stale), "slot_classes": dict(sorted(cls.items())),
        "rows_by_class": dict(sorted(Counter("FRESH" if n == 0 else f"STALE_{n}" if n is not None else "STALE_>"
                                             for n in row_stale).items())),
        "row_own_candle": dict(own_fresh),
        "row_created_minus_decision_seconds": quantiles(created_offset, (0.1, 0.5, 0.9, 0.99)),
        "candle_ingest_lag_after_close_seconds": quantiles(lag, (0.1, 0.5, 0.9, 0.99)),
        "candles_ingested_later_than_60s": int(sum(1 for x in lag if x > 60)),
        "candles_ingested_later_than_300s": int(sum(1 for x in lag if x > 300)),
        "candles_with_ingested_at": len(lag)}
    print(f"[audit] freshness {report['A_freshness']['slot_classes']}", flush=True)

    # B/C. replay + alignment -------------------------------------------------------
    frame = build_frame(closes, cfg, horizons_candles=[k], bars=bars if needs_bars(cfg) else None)
    col = {int(t): j for j, t in enumerate(frame["grid"])}
    row_of = {s: i for i, s in enumerate(frame["syms"])}
    label, excess, assets = frame["labels"][(k, mode)], frame["excess"][k], frame["assets"][k]
    min_assets = int(cfg["min_assets"])
    feats = frame["features"]
    recs = []
    for j, p in enumerate(preds):
        t = col.get(int(p[1].timestamp()) - step); i = row_of.get(p[2])
        if t is None or i is None or assets[t] < min_assets or not np.isfinite(label[i, t]) or abs(label[i, t]) <= 1e-9:
            continue
        recs.append((j, i, t))
    print(f"[audit] mature rows={len(recs)}; replaying", flush=True)
    by_exp = defaultdict(list)
    for r in recs:
        by_exp[preds[r[0]][0]].append(r)
    p_int = np.full(len(preds), np.nan); p_seen = np.full(len(preds), np.nan)
    abstain = Counter()
    for e, rs in by_exp.items():
        model = (exps.get(e) or {}).get("model")
        if model is None:
            abstain["artifact_missing"] += len(rs)
            continue
        for target, shift_of in ((p_int, lambda j: 0), (p_seen, lambda j: row_stale[j])):
            vecs, js = [], []
            for j, i, t in rs:
                s = shift_of(j)
                if s is None or t - s < 0:
                    abstain["seen_column_unknown"] += target is p_seen
                    continue
                cells = {f: {"value": float(feats[f][i, t - s]) if f in feats and np.isfinite(feats[f][i, t - s])
                             else None, "reason": None} for f in model.features}
                for c in cells.values():
                    if c["value"] is None:
                        c["reason"] = "nan"
                v = model.vector(cells, float("inf"))
                if v is None:
                    abstain["replay_abstained_" + ("intended" if target is p_int else "seen")] += 1
                    continue
                vecs.append(v); js.append(j)
            for j, pv in zip(js, model.predict(vecs)):
                target[j] = pv
    J = np.array([r[0] for r in recs]); I = np.array([r[1] for r in recs]); T = np.array([r[2] for r in recs])
    P = np.array([preds[j][3] for j in J], float)
    Y = (label[I, T] > 0).astype(int)
    D = np.array([preds[j][1].date().isoformat() for j in J], object)
    E = excess[I, T]
    S = np.array([-1 if row_stale[j] is None else row_stale[j] for j in J])
    groups = {"all": np.ones(len(J), bool), "fresh_slots": S == 0, "stale_slots": S != 0}
    B = {}
    for g, m in groups.items():
        B[g] = {"live": day_auc(P[m], Y[m], D[m]), "replay_intended": day_auc(p_int[J][m], Y[m], D[m]),
                "replay_seen": day_auc(p_seen[J][m], Y[m], D[m]),
                "corr_live_vs_replay_seen": corr(P[m], p_seen[J][m]),
                "corr_live_vs_replay_intended": corr(P[m], p_int[J][m])}
    report["B_replay"] = {"mature_rows": int(len(J)), "abstentions": dict(abstain), "groups": B,
                          "note": "replay uses complete candles; live medians over a partially ingested universe "
                                  "are not reproduced (residual mismatch shows in corr_live_vs_replay_seen)"}
    C = {}
    for shift in (-1, 0, 1):
        ok = (T + shift >= 0) & (T + shift < label.shape[1])
        lab = np.full(len(J), np.nan)
        lab[ok] = label[I[ok], T[ok] + shift]
        fin = np.isfinite(lab) & (np.abs(np.nan_to_num(lab)) > 1e-9)
        C[str(shift)] = {g: day_auc(P[m & fin], (lab[m & fin] > 0).astype(int), D[m & fin]) for g, m in groups.items()}
    report["C_label_alignment"] = {"meaning": "-1 = label of the previous decision (what a one-candle-stale feature "
                                              "would predict); 0 = official", "auc": C}

    # D/E. economics + effect ------------------------------------------------------
    research = eng.config(None)["research"]       # same source as pump_ml_live.live_evaluation
    q = float(research["walk_forward"]["economic_quantile"]); reps = int(research["bootstrap_repetitions"])
    report["D_economics"] = {"live": economics(P, E, D, q, reps, 7),
                             "replay_intended": economics(p_int[J], E, D, q, reps, 7),
                             "calibration": {"mean_p_live": round(float(P.mean()), 4),
                                             "realised_up_frequency": round(float(Y.mean()), 4)}}
    report["E_effect"] = effect_profile(P, ml)
    # F. what the model rewards: Spearman of the top features vs live p and vs the label
    from scipy.stats import spearmanr
    gains = {}
    for e in exps.values():
        for f, g in ((e["metrics"] or {}).get("feature_importance_gain") or {}).items():
            gains[f] = gains.get(f, 0.0) + float(g or 0)
    F = {}
    for f in sorted(gains, key=gains.get, reverse=True)[:args.top_features]:
        if f not in feats:
            continue
        x = feats[f][I, T]
        ok = np.isfinite(x)
        if ok.sum() < 30:
            continue
        F[f] = {"gain_sum": round(gains[f], 4), "rows": int(ok.sum()),
                "spearman_vs_p_live": round(float(spearmanr(x[ok], P[ok]).statistic), 4),
                "spearman_vs_label": round(float(spearmanr(x[ok], label[I, T][ok]).statistic), 4)}
    report["F_feature_direction"] = F
    return {**report, "status": "ok"}


async def run(args):
    from app.database import CeleryAsyncSessionLocal
    async with CeleryAsyncSessionLocal() as db:
        report = await execute(db, args)
    payload = json.dumps(report, indent=2, ensure_ascii=False, default=str)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(payload)
    print(payload)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--days", type=int, required=True)
    parser.add_argument("--max-back", type=int, default=4, help="staleness candles searched")
    parser.add_argument("--symbol-chunk", type=int, default=10)
    parser.add_argument("--top-features", type=int, default=6)
    parser.add_argument("--user-id")
    parser.add_argument("--output")
    asyncio.run(run(parser.parse_args()))
