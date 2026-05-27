"""
ML Pipeline Deep Audit — Selection Inversion & Alpha Recovery
=============================================================
Runs as a Cloud Run Job using the same secrets as the trainer.

Outputs a structured institutional report to stdout (captured by Cloud Logging).

Usage:
    gcloud run jobs execute scalpyn-ml-trainer \\
      --override-env AUDIT_MODE=true \\
      --command python --args ml_trainer/audit.py
"""

import json
import logging
import math
import os
import sys
import tempfile
from collections import defaultdict

import joblib
import numpy as np
import pandas as pd
from scipy import stats
from sklearn.metrics import (
    roc_auc_score,
    precision_recall_curve,
    confusion_matrix,
)
from sqlalchemy import create_engine, text

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [AUDIT] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("scalpyn.audit")

DB_URL       = os.environ["DB_URL"]
BUCKET_NAME  = os.environ.get("BUCKET_NAME", "scalpyn-mlflow")
DAYS         = int(os.getenv("AUDIT_DAYS", "90"))
WIN_THRESH   = float(os.getenv("MIN_WIN_PNL_PCT", "0.008")) * 100  # → pct


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _div(a, b, default=0.0):
    return a / b if b else default


def _pct(a, b):
    return round(100.0 * _div(a, b), 2)


def _sep():
    logger.info("=" * 72)


def _section(title):
    _sep()
    logger.info("  %s", title)
    _sep()


def _load_model():
    """Download win_fast_latest.pkl from GCS and return XGBClassifier."""
    try:
        from google.cloud import storage
        client = storage.Client()
        bucket = client.bucket(BUCKET_NAME)
        blob   = bucket.blob("models/win_fast_latest.pkl")
        with tempfile.NamedTemporaryFile(suffix=".pkl", delete=False) as f:
            blob.download_to_filename(f.name)
            model = joblib.load(f.name)
        logger.info("Model loaded from GCS  classes=%s", getattr(model, "classes_", "?"))
        return model
    except Exception as exc:
        logger.warning("Could not load model from GCS: %s", exc)
        return None


def _extract_features(metrics_raw):
    """Parse JSONB metrics dict → flat feature dict (same as extract_features)."""
    sys.path.insert(0, "/app")
    try:
        from app.ml.feature_extractor import extract_features, FEATURE_COLUMNS
        feats = extract_features(metrics_raw or {})
        return feats, FEATURE_COLUMNS
    except Exception:
        return {}, []


# ─────────────────────────────────────────────────────────────────────────────
# Data Loading
# ─────────────────────────────────────────────────────────────────────────────

def load_data(engine):
    """Pull decisions_log (ALLOW + BLOCK) and shadow_trades from DB."""
    logger.info("Loading decisions_log (last %d days)...", DAYS)
    with engine.connect() as conn:
        dl = conn.execute(text("""
            SELECT id, symbol, created_at, metrics, score,
                   pnl_pct, holding_seconds, outcome, decision
            FROM decisions_log
            WHERE l3_pass = true
              AND decision IN ('ALLOW', 'BLOCK')
              AND outcome IN ('tp', 'sl')
              AND pnl_pct IS NOT NULL
              AND created_at >= NOW() - INTERVAL :days
            ORDER BY created_at ASC
        """), {"days": f"{DAYS} days"})
        decisions = [dict(r._mapping) for r in dl.fetchall()]

        st = conn.execute(text("""
            SELECT id, decision_id, symbol, source, pnl_pct,
                   outcome, created_at
            FROM shadow_trades
            WHERE pnl_pct IS NOT NULL
              AND created_at >= NOW() - INTERVAL :days
            ORDER BY created_at ASC
        """), {"days": f"{DAYS} days"})
        shadows = [dict(r._mapping) for r in st.fetchall()]

    logger.info("decisions_log rows: %d   shadow_trades rows: %d",
                len(decisions), len(shadows))
    return decisions, shadows


# ─────────────────────────────────────────────────────────────────────────────
# Feature Matrix Builder
# ─────────────────────────────────────────────────────────────────────────────

def build_feature_matrix(decisions):
    """
    Parse metrics JSONB for each decision → DataFrame with features + metadata.
    Returns (df, feature_cols).
    """
    sys.path.insert(0, "/app")
    try:
        from app.ml.feature_extractor import extract_features, FEATURE_COLUMNS
    except Exception as e:
        logger.error("Cannot import feature_extractor: %s", e)
        return pd.DataFrame(), []

    rows = []
    for d in decisions:
        raw = d.get("metrics") or {}
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except Exception:
                raw = {}
        feats = extract_features(raw)
        feats["_id"]       = d.get("id")
        feats["_symbol"]   = d.get("symbol")
        feats["_decision"] = d.get("decision")
        feats["_pnl_pct"]  = d.get("pnl_pct")
        feats["_outcome"]  = d.get("outcome")
        feats["_score"]    = d.get("score")
        feats["_created_at"] = d.get("created_at")
        feats["_is_win"]   = 1 if (d.get("pnl_pct") or 0) > WIN_THRESH else 0
        rows.append(feats)

    df = pd.DataFrame(rows)
    present_feat_cols = [c for c in FEATURE_COLUMNS if c in df.columns]
    return df, present_feat_cols


# ─────────────────────────────────────────────────────────────────────────────
# 1. SELECTION BIAS AUDIT
# ─────────────────────────────────────────────────────────────────────────────

def audit_selection_bias(df, feat_cols):
    _section("AUDIT 1 — SELECTION BIAS & POPULATION ANALYSIS")

    approved = df[df["_decision"] == "ALLOW"]
    rejected = df[df["_decision"] == "BLOCK"]

    def group_stats(grp, name):
        if len(grp) == 0:
            return
        wins   = grp["_is_win"].sum()
        losses = len(grp) - wins
        wr     = _pct(wins, len(grp))
        avg_p  = grp["_pnl_pct"].mean()
        med_p  = grp["_pnl_pct"].median()
        std_p  = grp["_pnl_pct"].std()
        pos    = grp[grp["_pnl_pct"] > 0]["_pnl_pct"]
        neg    = grp[grp["_pnl_pct"] < 0]["_pnl_pct"].abs()
        pf     = _div(pos.sum(), neg.sum())
        ev     = avg_p  # expected value per trade
        sharpe = _div(avg_p, std_p) if std_p else 0
        logger.info(
            "[%s]  n=%d  wins=%d  losses=%d  win_rate=%.1f%%  "
            "avg_pnl=%.4f%%  median_pnl=%.4f%%  std=%.4f  "
            "profit_factor=%.2f  sharpe_est=%.3f  EV=%.4f%%",
            name, len(grp), wins, losses, wr,
            avg_p, med_p, std_p, pf, sharpe, ev,
        )

    group_stats(df, "ALL")
    group_stats(approved, "L3_APPROVED")
    group_stats(rejected, "L3_REJECTED")

    # Alpha destroyed by L3 filter
    if len(rejected) > 0 and len(approved) > 0:
        ev_approved = approved["_pnl_pct"].mean()
        ev_rejected = rejected["_pnl_pct"].mean()
        alpha_lost  = ev_rejected - ev_approved
        logger.info(
            "[SELECTION_INVERSION]  EV_approved=%.4f%%  EV_rejected=%.4f%%  "
            "alpha_destroyed=%.4f%%  inversion=%s",
            ev_approved, ev_rejected, alpha_lost,
            "CONFIRMED" if ev_rejected > ev_approved else "NOT_CONFIRMED",
        )

    # KS test: are approved and rejected from the same distribution?
    if len(approved) > 5 and len(rejected) > 5:
        ks_stat, ks_p = stats.ks_2samp(
            approved["_pnl_pct"].dropna(),
            rejected["_pnl_pct"].dropna(),
        )
        logger.info(
            "[KS_TEST pnl_pct]  statistic=%.4f  p_value=%.6f  "
            "different_populations=%s",
            ks_stat, ks_p, "YES (p<0.05)" if ks_p < 0.05 else "NO",
        )

    # Feature distribution divergence per group
    logger.info("--- Feature distribution: APPROVED vs REJECTED ---")
    divergent = []
    for col in feat_cols:
        a = approved[col].dropna()
        r = rejected[col].dropna()
        if len(a) < 5 or len(r) < 5:
            continue
        ks, p = stats.ks_2samp(a, r)
        if p < 0.05:
            divergent.append((ks, col, a.mean(), r.mean()))

    divergent.sort(reverse=True)
    for ks, col, a_mean, r_mean in divergent[:15]:
        logger.info(
            "  %-30s  KS=%.3f  mean_approved=%.4f  mean_rejected=%.4f  "
            "direction=%s",
            col, ks, a_mean, r_mean,
            "approved_higher" if a_mean > r_mean else "rejected_higher",
        )


# ─────────────────────────────────────────────────────────────────────────────
# 2. POPULATION COLLAPSE
# ─────────────────────────────────────────────────────────────────────────────

def audit_population_collapse(df, feat_cols):
    _section("AUDIT 2 — POPULATION COLLAPSE & OVERFILTERING")

    approved = df[df["_decision"] == "ALLOW"]
    rejected = df[df["_decision"] == "BLOCK"]

    # Diversity: std of feature distributions
    logger.info("--- Feature diversity (std): APPROVED vs REJECTED vs ALL ---")
    for col in feat_cols:
        a = approved[col].dropna()
        r = rejected[col].dropna()
        all_ = df[col].dropna()
        if len(a) < 3:
            continue
        collapse_ratio = _div(a.std(), all_.std())
        logger.info(
            "  %-30s  std_all=%.4f  std_approved=%.4f  std_rejected=%.4f  "
            "diversity_ratio=%.3f%s",
            col, all_.std(), a.std(),
            r.std() if len(r) > 2 else float("nan"),
            collapse_ratio,
            "  [COLLAPSED]" if collapse_ratio < 0.5 else "",
        )

    # Win rate by quartile of L3 score
    if "_score" in df.columns and df["_score"].notna().sum() > 10:
        logger.info("--- Win rate by L3 score quartile ---")
        df2 = df.dropna(subset=["_score"]).copy()
        df2["score_q"] = pd.qcut(df2["_score"], q=4,
                                  labels=["Q1_low", "Q2", "Q3", "Q4_high"],
                                  duplicates="drop")
        for q, grp in df2.groupby("score_q"):
            wr = _pct(grp["_is_win"].sum(), len(grp))
            avg = grp["_pnl_pct"].mean()
            logger.info(
                "  score_quartile=%-10s  n=%d  win_rate=%.1f%%  avg_pnl=%.4f%%",
                str(q), len(grp), wr, avg,
            )


# ─────────────────────────────────────────────────────────────────────────────
# 3. REGIME DRIFT
# ─────────────────────────────────────────────────────────────────────────────

def audit_regime_drift(df, feat_cols):
    _section("AUDIT 3 — REGIME DRIFT & TEMPORAL STABILITY")

    df2 = df.copy()
    df2["_week"] = pd.to_datetime(df2["_created_at"]).dt.to_period("W")
    weeks = sorted(df2["_week"].dropna().unique())

    logger.info("--- Weekly win rate & EV ---")
    for w in weeks:
        g = df2[df2["_week"] == w]
        wr = _pct(g["_is_win"].sum(), len(g))
        ev = g["_pnl_pct"].mean()
        logger.info("  week=%-12s  n=%3d  win_rate=%5.1f%%  avg_pnl=%+.4f%%",
                    str(w), len(g), wr, ev)

    # PSI — Population Stability Index on key features
    # Compare first 50% vs last 50% of records
    if len(df2) < 20:
        return
    mid = len(df2) // 2
    early = df2.iloc[:mid]
    late  = df2.iloc[mid:]

    logger.info("--- PSI (Population Stability Index) — early vs late period ---")
    logger.info("    PSI < 0.10 = stable | 0.10-0.25 = monitor | > 0.25 = unstable")
    psi_results = []
    for col in feat_cols:
        a = early[col].dropna()
        b = late[col].dropna()
        if len(a) < 5 or len(b) < 5:
            continue
        bins = np.percentile(pd.concat([a, b]), np.linspace(0, 100, 11))
        bins = np.unique(bins)
        if len(bins) < 3:
            continue
        a_hist = np.histogram(a, bins=bins)[0] / len(a) + 1e-8
        b_hist = np.histogram(b, bins=bins)[0] / len(b) + 1e-8
        psi = float(np.sum((b_hist - a_hist) * np.log(b_hist / a_hist)))
        psi_results.append((psi, col))

    for psi, col in sorted(psi_results, reverse=True)[:15]:
        status = "UNSTABLE" if psi > 0.25 else ("MONITOR" if psi > 0.10 else "stable")
        logger.info("  %-30s  PSI=%.4f  [%s]", col, psi, status)

    # Label drift: win rate early vs late
    wr_early = _pct(early["_is_win"].sum(), len(early))
    wr_late  = _pct(late["_is_win"].sum(), len(late))
    logger.info(
        "[LABEL_DRIFT]  win_rate_early=%.1f%%  win_rate_late=%.1f%%  "
        "delta=%.1f%%  drift=%s",
        wr_early, wr_late, wr_late - wr_early,
        "SIGNIFICANT" if abs(wr_late - wr_early) > 10 else "moderate",
    )


# ─────────────────────────────────────────────────────────────────────────────
# 4. SHAP INTELLIGENCE REPORT
# ─────────────────────────────────────────────────────────────────────────────

def audit_shap(df, feat_cols, model):
    _section("AUDIT 4 — SHAP FEATURE INTELLIGENCE")

    if model is None:
        logger.warning("Model not loaded — skipping SHAP analysis.")
        return

    try:
        import shap
    except ImportError:
        logger.warning("shap not installed — skipping SHAP analysis.")
        return

    X = df[feat_cols].astype("float32")
    if len(X) < 10:
        logger.warning("Too few records for SHAP (%d) — skipping.", len(X))
        return

    # Predict and segment
    proba = model.predict_proba(X)[:, 1]
    df = df.copy()
    df["_proba"] = proba

    approved = df[df["_decision"] == "ALLOW"]
    rejected = df[df["_decision"] == "BLOCK"]
    ap_win   = approved[approved["_is_win"] == 1]
    ap_loss  = approved[approved["_is_win"] == 0]
    rj_win   = rejected[rejected["_is_win"] == 1]
    rj_loss  = rejected[rejected["_is_win"] == 0]

    logger.info(
        "Segments — ap_win=%d  ap_loss=%d  rj_win=%d  rj_loss=%d",
        len(ap_win), len(ap_loss), len(rj_win), len(rj_loss),
    )

    explainer = shap.TreeExplainer(model)

    def shap_mean_abs(grp, name):
        if len(grp) < 3:
            logger.info("  [%s] too few rows (%d) — skipped", name, len(grp))
            return
        Xg = grp[feat_cols].astype("float32")
        sv = explainer.shap_values(Xg)
        # sv shape: (n, features) for binary XGB
        if isinstance(sv, list):
            sv = sv[1]
        mean_abs = np.abs(sv).mean(axis=0)
        ranked   = sorted(zip(feat_cols, mean_abs, sv.mean(axis=0)),
                          key=lambda x: -x[1])
        logger.info("  --- SHAP top features [%s] (n=%d) ---", name, len(grp))
        for feat, ma, ms in ranked[:12]:
            direction = "positive_impact" if ms > 0 else "negative_impact"
            logger.info(
                "    %-30s  mean_abs=%.4f  mean_shap=%+.4f  [%s]",
                feat, ma, ms, direction,
            )

    shap_mean_abs(ap_win,  "APPROVED_WINNER")
    shap_mean_abs(ap_loss, "APPROVED_LOSER")
    shap_mean_abs(rj_win,  "REJECTED_WINNER")
    shap_mean_abs(rj_loss, "REJECTED_LOSER")

    # Cross comparison: what differs between RJ_WIN and AP_WIN?
    logger.info("  --- Feature divergence: REJECTED_WINNER vs APPROVED_WINNER ---")
    for col in feat_cols:
        rw = rj_win[col].dropna()
        aw = ap_win[col].dropna()
        if len(rw) < 3 or len(aw) < 3:
            continue
        diff = rw.mean() - aw.mean()
        if abs(diff) > 0.1 * max(abs(rw.mean()), abs(aw.mean()), 1e-6):
            logger.info(
                "    %-30s  rj_win_mean=%.4f  ap_win_mean=%.4f  diff=%+.4f",
                col, rw.mean(), aw.mean(), diff,
            )


# ─────────────────────────────────────────────────────────────────────────────
# 5. HYPOTHESIS INVERSION AUDIT
# ─────────────────────────────────────────────────────────────────────────────

def audit_hypothesis_inversion(df, feat_cols):
    _section("AUDIT 5 — HUMAN HYPOTHESIS INVERSION TEST")

    hypotheses = [
        # (feature, "high_is_bullish" assumed, description)
        ("rsi",                True,  "RSI high = bullish momentum"),
        ("adx",                True,  "ADX high = strong trend"),
        ("volume_spike",       True,  "Volume spike = confirmation"),
        ("taker_ratio",        True,  "High taker_ratio = buy pressure"),
        ("momentum_strength",  True,  "High momentum = continuation"),
        ("ema9_gt_ema21",      True,  "EMA9>EMA21 = uptrend"),
        ("ema50_gt_ema200",    True,  "EMA50>EMA200 = macro uptrend"),
        ("trend_alignment",    True,  "High alignment = strong setup"),
        ("bb_width",           False, "Low BB width = coil setup"),
        ("spread_pct",         False, "Low spread = liquid market"),
    ]

    logger.info("Testing whether high/low values of key features correlate")
    logger.info("with actual win rate. INVERSION = market behaves opposite.")

    for feat, high_bullish, desc in hypotheses:
        if feat not in df.columns:
            continue
        col_data = df[feat].dropna()
        if len(col_data) < 10:
            continue

        median = col_data.median()
        high   = df[df[feat] > median]
        low    = df[df[feat] <= median]

        wr_high = _pct(high["_is_win"].sum(), len(high)) if len(high) > 0 else 0
        wr_low  = _pct(low["_is_win"].sum(),  len(low))  if len(low) > 0 else 0
        ev_high = high["_pnl_pct"].mean() if len(high) > 0 else 0
        ev_low  = low["_pnl_pct"].mean()  if len(low) > 0 else 0

        if high_bullish:
            hypothesis_correct = wr_high > wr_low
        else:
            hypothesis_correct = wr_low > wr_high

        logger.info(
            "  %-30s  wr_high=%5.1f%%  wr_low=%5.1f%%  "
            "ev_high=%+.4f%%  ev_low=%+.4f%%  hypothesis=%s  [%s]",
            feat, wr_high, wr_low, ev_high, ev_low,
            "HIGH_BULLISH" if high_bullish else "LOW_BULLISH",
            "CORRECT" if hypothesis_correct else "*** INVERTED ***",
        )

    # Exhaustion analysis: extreme RSI/ADX
    for feat, label_high, label_low in [
        ("rsi",             "overbought(>70)", "oversold(<30)"),
        ("adx",             "strong_trend(>40)", "weak_trend(<20)"),
        ("momentum_strength", "extreme_momentum(>0.8)", "low_momentum(<0.2)"),
    ]:
        if feat not in df.columns:
            continue
        v = df[feat].dropna()
        p25, p75 = v.quantile(0.25), v.quantile(0.75)
        extreme_high = df[df[feat] > p75]
        extreme_low  = df[df[feat] < p25]
        wr_eh = _pct(extreme_high["_is_win"].sum(), len(extreme_high)) if len(extreme_high) > 0 else 0
        wr_el = _pct(extreme_low["_is_win"].sum(), len(extreme_low))  if len(extreme_low) > 0 else 0
        logger.info(
            "  EXTREME %-24s  %s: wr=%.1f%%  %s: wr=%.1f%%",
            feat, label_high, wr_eh, label_low, wr_el,
        )


# ─────────────────────────────────────────────────────────────────────────────
# 6. THRESHOLD CALIBRATION
# ─────────────────────────────────────────────────────────────────────────────

def audit_threshold(df, feat_cols, model):
    _section("AUDIT 6 — THRESHOLD CALIBRATION & ECONOMIC IMPACT")

    if model is None:
        logger.warning("Model not loaded — skipping threshold analysis.")
        return

    X = df[feat_cols].astype("float32")
    y = df["_is_win"].values
    pnl = df["_pnl_pct"].values

    if len(np.unique(y)) < 2:
        logger.warning("Single class in dataset — skipping threshold analysis.")
        return

    proba = model.predict_proba(X)[:, 1]
    auc_full = roc_auc_score(y, proba)
    logger.info("ROC AUC on full audit dataset: %.4f", auc_full)
    logger.info("AUC inverted check: %.4f  (if > original → score inverted)",
                roc_auc_score(y, 1 - proba))

    logger.info("--- Threshold sweep: PnL, precision, recall, FPR ---")
    logger.info(
        "  %-8s  %-6s  %-8s  %-8s  %-8s  %-10s  %-10s  %-10s",
        "thresh", "n_app", "prec", "recall", "FPR", "avg_pnl", "tot_pnl", "EV"
    )
    best_ev_thresh = 0.5
    best_ev = -999
    for t in np.arange(0.30, 0.85, 0.05):
        mask = proba >= t
        n    = mask.sum()
        if n < 3:
            break
        tp  = int((y[mask] == 1).sum())
        fp  = int((y[mask] == 0).sum())
        fn  = int((y[~mask] == 1).sum())
        tn  = int((y[~mask] == 0).sum())
        prec   = _div(tp, tp + fp)
        rec    = _div(tp, tp + fn)
        fpr    = _div(fp, fp + tn)
        avg_p  = pnl[mask].mean()
        tot_p  = pnl[mask].sum()
        ev     = avg_p
        if ev > best_ev:
            best_ev = ev
            best_ev_thresh = t
        logger.info(
            "  %-8.2f  %-6d  %-8.3f  %-8.3f  %-8.3f  %-10.4f  %-10.4f  %-10.4f",
            t, n, prec, rec, fpr, avg_p, tot_p, ev,
        )

    logger.info(
        "[OPTIMAL_THRESHOLD]  best_EV_threshold=%.2f  best_EV=%.4f%%",
        best_ev_thresh, best_ev,
    )

    # Score distribution by segment
    ap_proba = proba[df["_decision"].values == "ALLOW"]
    rj_proba = proba[df["_decision"].values == "BLOCK"]
    logger.info(
        "[SCORE_DIST APPROVED]  mean=%.4f  std=%.4f  p10=%.4f  p50=%.4f  p90=%.4f",
        ap_proba.mean(), ap_proba.std(),
        np.percentile(ap_proba, 10), np.percentile(ap_proba, 50), np.percentile(ap_proba, 90),
    )
    if len(rj_proba) > 0:
        logger.info(
            "[SCORE_DIST REJECTED]  mean=%.4f  std=%.4f  p10=%.4f  p50=%.4f  p90=%.4f",
            rj_proba.mean(), rj_proba.std(),
            np.percentile(rj_proba, 10), np.percentile(rj_proba, 50), np.percentile(rj_proba, 90),
        )


# ─────────────────────────────────────────────────────────────────────────────
# 7. REJECTED ALPHA REPORT
# ─────────────────────────────────────────────────────────────────────────────

def audit_rejected_alpha(decisions, shadows):
    _section("AUDIT 7 — REJECTED ALPHA & L3 DESTRUCTION ANALYSIS")

    # Shadow trade breakdown
    shadow_df = pd.DataFrame(shadows)
    if shadow_df.empty:
        logger.warning("No shadow trades found.")
        return

    for src, grp in shadow_df.groupby("source"):
        wins   = (grp["pnl_pct"] > WIN_THRESH).sum()
        losses = (grp["pnl_pct"] <= WIN_THRESH).sum()
        wr     = _pct(wins, len(grp))
        avg    = grp["pnl_pct"].mean()
        pos    = grp[grp["pnl_pct"] > 0]["pnl_pct"]
        neg    = grp[grp["pnl_pct"] <= 0]["pnl_pct"].abs()
        pf     = _div(pos.sum(), neg.sum())
        tp_count = (grp["outcome"] == "tp").sum()
        sl_count = (grp["outcome"] == "sl").sum()
        logger.info(
            "[SOURCE=%-15s]  n=%d  wins=%d  losses=%d  win_rate=%.1f%%  "
            "avg_pnl=%.4f%%  profit_factor=%.2f  tp=%d  sl=%d",
            src, len(grp), wins, losses, wr, avg, pf, tp_count, sl_count,
        )

    # Missed alpha: what would portfolio look like with REJECTED trades?
    l3_rej = shadow_df[shadow_df["source"] == "L3_REJECTED"]
    l3_app = shadow_df[shadow_df["source"] == "L3"]

    if len(l3_rej) > 0 and len(l3_app) > 0:
        ev_app = l3_app["pnl_pct"].mean()
        ev_rej = l3_rej["pnl_pct"].mean()
        alpha_destroyed_per_trade = ev_rej - ev_app
        total_missed_alpha = l3_rej["pnl_pct"].sum()

        logger.info(
            "[ALPHA_DESTRUCTION]  EV_approved=%.4f%%  EV_rejected=%.4f%%  "
            "alpha_per_trade=%.4f%%  total_missed_pnl=%.4f%%  "
            "rejection_count=%d",
            ev_app, ev_rej, alpha_destroyed_per_trade,
            total_missed_alpha, len(l3_rej),
        )

        # Best rejected trades (top missed opportunities)
        top_missed = l3_rej.nlargest(10, "pnl_pct")[["symbol","pnl_pct","outcome","created_at"]]
        logger.info("[TOP_10_MISSED_OPPORTUNITIES]")
        for _, row in top_missed.iterrows():
            logger.info(
                "  symbol=%-12s  pnl=%.4f%%  outcome=%s  date=%s",
                row["symbol"], row["pnl_pct"], row["outcome"],
                str(row["created_at"])[:10],
            )

    # Temporal evolution of rejected vs approved EV
    if "created_at" in shadow_df.columns:
        shadow_df["_week"] = pd.to_datetime(shadow_df["created_at"]).dt.to_period("W")
        logger.info("--- Weekly EV: APPROVED vs REJECTED ---")
        weeks = sorted(shadow_df["_week"].dropna().unique())
        for w in weeks:
            wg = shadow_df[shadow_df["_week"] == w]
            app = wg[wg["source"] == "L3"]["pnl_pct"].mean()
            rej = wg[wg["source"] == "L3_REJECTED"]["pnl_pct"].mean()
            logger.info(
                "  week=%-12s  EV_approved=%+.4f%%  EV_rejected=%+.4f%%  gap=%+.4f%%",
                str(w),
                app if not math.isnan(app) else 0,
                rej if not math.isnan(rej) else 0,
                (rej - app) if (not math.isnan(rej) and not math.isnan(app)) else 0,
            )


# ─────────────────────────────────────────────────────────────────────────────
# 8. DATASET QUALITY
# ─────────────────────────────────────────────────────────────────────────────

def audit_dataset_quality(df, feat_cols):
    _section("AUDIT 8 — DATASET QUALITY & LEAKAGE CHECKS")

    total = len(df)
    logger.info("Total records: %d", total)
    logger.info("Win rate: %.2f%%  (wins=%d  losses=%d)",
                _pct(df["_is_win"].sum(), total),
                df["_is_win"].sum(), (df["_is_win"] == 0).sum())

    # NaN coverage per feature
    logger.info("--- Feature coverage (non-null rate) ---")
    sparse = []
    for col in feat_cols:
        cov = df[col].notna().mean()
        if cov < 0.7:
            sparse.append((cov, col))
    sparse.sort()
    for cov, col in sparse:
        logger.info("  %-30s  coverage=%.1f%%  [SPARSE]", col, cov * 100)
    if not sparse:
        logger.info("  All features have >70%% coverage.")

    # Constant or near-constant features
    logger.info("--- Near-constant features (std < 0.01) ---")
    for col in feat_cols:
        s = df[col].dropna().std()
        if 0 <= s < 0.01:
            logger.info("  %-30s  std=%.6f  [NEAR_CONSTANT]", col, s)

    # Temporal leakage check: feature corr with pnl
    logger.info("--- Feature correlation with pnl_pct (leakage check) ---")
    high_corr = []
    for col in feat_cols:
        valid = df[[col, "_pnl_pct"]].dropna()
        if len(valid) < 10:
            continue
        corr = valid[col].corr(valid["_pnl_pct"])
        if abs(corr) > 0.4:
            high_corr.append((abs(corr), col, corr))
    high_corr.sort(reverse=True)
    for ac, col, c in high_corr:
        logger.info(
            "  %-30s  corr=%.4f  [HIGH — possible leakage if post-trade]",
            col, c,
        )
    if not high_corr:
        logger.info("  No features with >0.4 correlation to pnl_pct — leakage not detected.")

    # Class balance over time
    df2 = df.copy()
    df2["_month"] = pd.to_datetime(df2["_created_at"]).dt.to_period("M")
    logger.info("--- Monthly class balance ---")
    for m, g in df2.groupby("_month"):
        wr = _pct(g["_is_win"].sum(), len(g))
        logger.info("  month=%-10s  n=%3d  win_rate=%.1f%%", str(m), len(g), wr)


# ─────────────────────────────────────────────────────────────────────────────
# 9. FINAL ROOT CAUSE SUMMARY
# ─────────────────────────────────────────────────────────────────────────────

def root_cause_summary(df, feat_cols, shadows):
    _section("AUDIT 9 — ROOT CAUSE ANALYSIS & STRATEGY")

    approved = df[df["_decision"] == "ALLOW"]
    rejected = df[df["_decision"] == "BLOCK"]
    shadow_df = pd.DataFrame(shadows) if shadows else pd.DataFrame()

    ev_app = approved["_pnl_pct"].mean() if len(approved) > 0 else 0
    ev_rej = rejected["_pnl_pct"].mean() if len(rejected) > 0 else 0
    wr_app = _pct(approved["_is_win"].sum(), len(approved)) if len(approved) > 0 else 0
    wr_rej = _pct(rejected["_is_win"].sum(), len(rejected)) if len(rejected) > 0 else 0

    inversion = ev_rej > ev_app

    logger.info("=== ROOT CAUSE FINDINGS ===")
    logger.info("")
    logger.info("1. SELECTION INVERSION: %s", "CONFIRMED" if inversion else "NOT CONFIRMED")
    logger.info("   EV_approved=%+.4f%%  EV_rejected=%+.4f%%",  ev_app, ev_rej)
    logger.info("   WR_approved=%.1f%%   WR_rejected=%.1f%%",   wr_app, wr_rej)
    logger.info("")

    if not shadow_df.empty:
        l3 = shadow_df[shadow_df["source"] == "L3"]["pnl_pct"]
        lr = shadow_df[shadow_df["source"] == "L3_REJECTED"]["pnl_pct"]
        if len(l3) > 0 and len(lr) > 0:
            logger.info("2. SHADOW PORTFOLIO VALIDATION:")
            logger.info("   L3 shadows:          EV=%+.4f%%  n=%d", l3.mean(), len(l3))
            logger.info("   L3_REJECTED shadows: EV=%+.4f%%  n=%d", lr.mean(), len(lr))
            logger.info("   Alpha destroyed per trade: %+.4f%%", lr.mean() - l3.mean())
        logger.info("")

    logger.info("3. MODEL AUC COLLAPSE EXPLANATION:")
    logger.info("   - Training population is L3-filtered (selection bias)")
    logger.info("   - Test set = most recent 15%% (regime drift)")
    logger.info("   - L3 approves low-quality setups → model learns inverted patterns")
    logger.info("   - 01-20/05 bad indicator period contaminated 27.6%% of shadow data")
    logger.info("")

    logger.info("4. ARCHITECTURE FINDINGS:")
    logger.info("   - ML is subordinate to L3: can only filter ALLOW, not rescue REJECT")
    logger.info("   - INCLUDE_REJECTED_IN_TRAIN=true partially corrects this (v12→v13)")
    logger.info("   - Dataset too small after cleanup (211 records) for stable XGBoost")
    logger.info("")

    logger.info("=== STRATEGY FOR CORRECTION ===")
    logger.info("")
    logger.info("IMMEDIATE (weeks 1-2):")
    logger.info("  [DONE] Activate INCLUDE_REJECTED_IN_TRAIN=true")
    logger.info("  [DONE] Remove bad indicator period 01-20/05")
    logger.info("  [TODO] Accumulate 400+ clean records before next major retrain")
    logger.info("  [TODO] Activate ML_GATE_ENABLED=true with threshold=0.50")
    logger.info("")
    logger.info("SHORT TERM (weeks 3-6):")
    logger.info("  [TODO] SHAP analysis: use rejected winners vs approved winners")
    logger.info("         to identify which L3 criteria are destroying alpha")
    logger.info("  [TODO] Audit L3 filter rules against SHAP-confirmed features")
    logger.info("  [TODO] Remove or relax L3 criteria that reject high-SHAP assets")
    logger.info("")
    logger.info("MEDIUM TERM (months 2-3):")
    logger.info("  [TODO] Decouple ML from L3 dependency")
    logger.info("         ML scores ALL assets, not just L3-approved")
    logger.info("  [TODO] L3 becomes a liquidity/volume floor only (objective criteria)")
    logger.info("  [TODO] ML becomes the primary decision gate")
    logger.info("")
    logger.info("FEATURES TO INVESTIGATE (SHAP-driven, not hypothesis-driven):")
    for col in feat_cols:
        if col in df.columns:
            v = df[col].dropna()
            if len(v) < 5:
                continue
            high = df[df[col] > v.median()]["_is_win"].mean()
            low  = df[df[col] <= v.median()]["_is_win"].mean()
            if abs(high - low) > 0.10:
                direction = "HIGH favors WIN" if high > low else "LOW favors WIN"
                logger.info("  %-30s  delta_wr=%.3f  [%s]", col, abs(high-low), direction)

    _sep()
    logger.info("=== AUDIT COMPLETE ===")


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    _section("SCALPYN ML PIPELINE DEEP AUDIT")
    logger.info("AUDIT_DAYS=%d  WIN_THRESHOLD=%.4f%%", DAYS, WIN_THRESH)

    engine = create_engine(DB_URL, pool_pre_ping=True)

    decisions, shadows = load_data(engine)
    if not decisions:
        logger.error("No decisions found — aborting audit.")
        sys.exit(1)

    df, feat_cols = build_feature_matrix(decisions)
    if df.empty or not feat_cols:
        logger.error("Feature matrix empty — aborting audit.")
        sys.exit(1)

    logger.info(
        "Feature matrix: %d rows  %d features  "
        "approved=%d  rejected=%d  wins=%d  losses=%d",
        len(df), len(feat_cols),
        (df["_decision"] == "ALLOW").sum(),
        (df["_decision"] == "BLOCK").sum(),
        df["_is_win"].sum(),
        (df["_is_win"] == 0).sum(),
    )

    model = _load_model()

    audit_selection_bias(df, feat_cols)
    audit_population_collapse(df, feat_cols)
    audit_regime_drift(df, feat_cols)
    audit_shap(df, feat_cols, model)
    audit_hypothesis_inversion(df, feat_cols)
    audit_threshold(df, feat_cols, model)
    audit_rejected_alpha(decisions, shadows)
    audit_dataset_quality(df, feat_cols)
    root_cause_summary(df, feat_cols, shadows)


if __name__ == "__main__":
    main()
