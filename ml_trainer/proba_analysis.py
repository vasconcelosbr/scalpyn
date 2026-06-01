"""
ML Probability Distribution Analysis — v27 (win_fast_latest.pkl)
=================================================================
Carrega o modelo ativo do GCS, reconstrói o dataset de shadow_trades
e produz análise completa de distribuição de probabilidades.

Executar via Cloud Run Job:
    gcloud run jobs execute scalpyn-ml-trainer \
      --update-env-vars PROBA_ANALYSIS_MODE=true \
      --project clickrate-477217 --region us-central1 --wait
"""

import json
import logging
import os
import sys
import tempfile

import joblib
import numpy as np
import pandas as pd
from sklearn.metrics import (
    confusion_matrix,
    roc_auc_score,
    precision_score,
    recall_score,
    f1_score,
)
from sqlalchemy import create_engine, text

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [PROBA] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("scalpyn.proba_analysis")

DB_URL      = os.environ["DB_URL"]
BUCKET_NAME = os.environ.get("BUCKET_NAME", "scalpyn-mlflow")
DAYS        = int(os.getenv("DAYS_LOOKBACK", "90"))
ML_SOURCE   = os.getenv("ML_SOURCE_FILTER", "L3")
EXCL_FROM   = os.getenv("TRAIN_EXCLUDE_FROM", "")
EXCL_TO     = os.getenv("TRAIN_EXCLUDE_TO", "")


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _sep(char="=", width=72):
    logger.info(char * width)

def _section(title):
    _sep()
    logger.info("  %s", title)
    _sep()

def _div(a, b, default=0.0):
    return a / b if b else default


# ─────────────────────────────────────────────────────────────────────────────
# Load model
# ─────────────────────────────────────────────────────────────────────────────

def _load_model():
    from google.cloud import storage
    client = storage.Client()
    bucket = client.bucket(BUCKET_NAME)
    blob   = bucket.blob("models/win_fast_latest.pkl")
    with tempfile.NamedTemporaryFile(suffix=".pkl", delete=False) as f:
        blob.download_to_filename(f.name)
        model = joblib.load(f.name)
    logger.info("Model loaded  classes=%s  n_features=%s",
                getattr(model, "classes_", "?"),
                getattr(model, "n_features_in_", "?"))
    return model


# ─────────────────────────────────────────────────────────────────────────────
# Load data (same query as job.py)
# ─────────────────────────────────────────────────────────────────────────────

def _load_records(engine):
    excl_clause  = ""
    excl_params: dict = {}
    if EXCL_FROM and EXCL_TO:
        excl_clause = "AND NOT (created_at >= :excl_from AND created_at <= :excl_to)"
        excl_params = {"excl_from": EXCL_FROM, "excl_to": f"{EXCL_TO} 23:59:59"}

    with engine.connect() as conn:
        result = conn.execute(text(f"""
            SELECT symbol, source, pnl_pct, holding_seconds, outcome,
                   features_snapshot, created_at,
                   ttt_outcome, ttt_fast_win_bucket,
                   time_to_tp_minutes, elapsed_minutes, profit_velocity
            FROM shadow_trades
            WHERE source = :source
              AND outcome IN ('TP_HIT', 'SL_HIT', 'TIMEOUT')
              AND pnl_pct IS NOT NULL
              AND features_snapshot IS NOT NULL
              AND created_at >= NOW() - INTERVAL :days
              {excl_clause}
            ORDER BY created_at ASC
        """), {"source": ML_SOURCE, "days": f"{DAYS} days", **excl_params})
        records = [dict(r._mapping) for r in result.fetchall()]

    logger.info("shadow_trades loaded: %d records (source=%s, days=%d, excl=%s→%s)",
                len(records), ML_SOURCE, DAYS,
                EXCL_FROM or "none", EXCL_TO or "none")
    return records


# ─────────────────────────────────────────────────────────────────────────────
# ASCII histogram
# ─────────────────────────────────────────────────────────────────────────────

def _ascii_histogram(proba: np.ndarray, bins: int = 20, width: int = 50):
    counts, edges = np.histogram(proba, bins=bins, range=(0.0, 1.0))
    max_count = counts.max() if counts.max() > 0 else 1
    logger.info("  Prob range  |  Count  | Distribution")
    logger.info("  -----------+---------+" + "-" * width)
    for i, (lo, hi, cnt) in enumerate(zip(edges[:-1], edges[1:], counts)):
        bar = "#" * int(cnt / max_count * width)
        logger.info("  [%.2f-%.2f) | %6d  | %s", lo, hi, cnt, bar)


# ─────────────────────────────────────────────────────────────────────────────
# Confusion matrix display
# ─────────────────────────────────────────────────────────────────────────────

def _show_confusion(y_true, y_pred, threshold):
    tn, fp, fn, tp = confusion_matrix(y_true, y_pred, labels=[0, 1]).ravel()
    prec  = _div(tp, tp + fp)
    rec   = _div(tp, tp + fn)
    fpr   = _div(fp, fp + tn)
    spec  = _div(tn, tn + fp)
    f1    = _div(2 * prec * rec, prec + rec)
    acc   = _div(tp + tn, tp + tn + fp + fn)
    n_app = tp + fp
    logger.info("  threshold=%.2f | approved=%d/%d",
                threshold, n_app, len(y_true))
    logger.info("              Pred=0    Pred=1")
    logger.info("  True=0   TN=%5d  FP=%5d   FPR=%.3f  Specificity=%.3f",
                tn, fp, fpr, spec)
    logger.info("  True=1   FN=%5d  TP=%5d   Recall=%.3f",
                fn, tp, rec)
    logger.info("  Precision=%.4f  Recall=%.4f  F1=%.4f  Accuracy=%.4f",
                prec, rec, f1, acc)


# ─────────────────────────────────────────────────────────────────────────────
# Main analysis
# ─────────────────────────────────────────────────────────────────────────────

def main():
    _section("PROBA ANALYSIS — win_fast_latest.pkl (v27)")

    engine = create_engine(DB_URL, pool_pre_ping=True)
    model  = _load_model()

    records = _load_records(engine)
    if not records:
        logger.warning("No records found — aborting.")
        return

    # Build feature matrix (same pipeline as trainer)
    sys.path.insert(0, "/app")
    from app.ml.feature_extractor import build_training_dataframe, FEATURE_COLUMNS

    df = build_training_dataframe(records)
    logger.info("DataFrame: %d rows, %d cols", len(df), len(df.columns))

    # Align features with model
    _nan = float("nan")
    n_features_in = getattr(model, "n_features_in_", len(FEATURE_COLUMNS))
    feat_cols = FEATURE_COLUMNS[:n_features_in]

    X      = np.array([[row.get(f, _nan) for f in feat_cols] for _, row in df.iterrows()],
                       dtype="float32")
    y_true = df["is_win_fast"].to_numpy(dtype=int)
    pnl    = df["_pnl_pct"].to_numpy(dtype=float) if "_pnl_pct" in df.columns else np.zeros(len(df))

    proba_1 = model.predict_proba(X)[:, 1]   # P(win)
    proba_0 = model.predict_proba(X)[:, 0]   # P(loss)

    logger.info("Inference complete: %d samples", len(proba_1))

    # ── 1. OVERALL DISTRIBUTION ──────────────────────────────────────────────
    _section("1. DISTRIBUICAO COMPLETA DAS PROBABILIDADES (proba[:,1])")
    logger.info("  count  = %d", len(proba_1))
    logger.info("  mean   = %.6f", proba_1.mean())
    logger.info("  std    = %.6f", proba_1.std())
    logger.info("  min    = %.6f", proba_1.min())
    logger.info("  max    = %.6f", proba_1.max())
    logger.info("  AUC (proba[:,1])   = %.6f", roc_auc_score(y_true, proba_1))
    logger.info("  AUC (1-proba[:,1]) = %.6f", roc_auc_score(y_true, 1 - proba_1))
    logger.info("  AUC (proba[:,0])   = %.6f", roc_auc_score(y_true, proba_0))
    logger.info("  AUC (1-proba[:,0]) = %.6f", roc_auc_score(y_true, 1 - proba_0))

    # ── 2. HISTOGRAM ─────────────────────────────────────────────────────────
    _section("2. HISTOGRAMA predict_proba[:,1]")
    _ascii_histogram(proba_1, bins=20)

    # ── 3. PERCENTILES ───────────────────────────────────────────────────────
    _section("3. PERCENTIS")
    for p in [1, 5, 10, 25, 50, 75, 90, 95, 99]:
        logger.info("  p%02d = %.6f", p, np.percentile(proba_1, p))

    # Percentis separados por classe
    w1 = proba_1[y_true == 1]
    w0 = proba_1[y_true == 0]
    logger.info("")
    logger.info("  --- Por classe real ---")
    logger.info("  y_true=1 (wins):  n=%d  mean=%.4f  median=%.4f  min=%.4f  max=%.4f",
                len(w1), w1.mean() if len(w1) else 0,
                np.median(w1) if len(w1) else 0,
                w1.min() if len(w1) else 0,
                w1.max() if len(w1) else 0)
    if len(w1):
        for p in [10, 25, 50, 75, 90]:
            logger.info("    y=1 p%02d = %.6f", p, np.percentile(w1, p))
    logger.info("  y_true=0 (losses): n=%d  mean=%.4f  median=%.4f  min=%.4f  max=%.4f",
                len(w0), w0.mean() if len(w0) else 0,
                np.median(w0) if len(w0) else 0,
                w0.min() if len(w0) else 0,
                w0.max() if len(w0) else 0)
    if len(w0):
        for p in [10, 25, 50, 75, 90]:
            logger.info("    y=0 p%02d = %.6f", p, np.percentile(w0, p))
    if len(w1) and len(w0):
        direction = "NORMAL (wins > losses)" if w1.mean() > w0.mean() else "INVERTIDO (losses > wins)"
        logger.info("  RANKING: mean(y=1)=%.4f  mean(y=0)=%.4f  => %s",
                    w1.mean(), w0.mean(), direction)

    # ── 4. APROVADOS POR THRESHOLD ───────────────────────────────────────────
    _section("4. TRADES APROVADOS POR THRESHOLD")
    logger.info("  %-8s  %-8s  %-8s  %-8s  %-8s", "thresh", "approved", "pct_tot", "wins", "losses")
    for t in [0.10, 0.20, 0.30, 0.40, 0.50, 0.60, 0.70, 0.80, 0.90]:
        mask   = proba_1 >= t
        n_app  = mask.sum()
        n_win  = int((y_true[mask] == 1).sum())
        n_loss = int((y_true[mask] == 0).sum())
        pct    = 100.0 * n_app / len(proba_1) if len(proba_1) else 0
        logger.info("  %-8.2f  %-8d  %-7.1f%%  %-8d  %-8d",
                    t, n_app, pct, n_win, n_loss)

    # ── 5–8. CURVAS threshold × métricas ─────────────────────────────────────
    _section("5-8. CURVAS: threshold x precision / recall / FPR / pnl")
    logger.info("  %-7s  %-10s  %-10s  %-10s  %-12s  %-12s  %-8s",
                "thresh", "precision", "recall", "FPR", "avg_pnl%", "tot_pnl%", "approved")

    thresholds = np.round(np.arange(0.05, 0.96, 0.05), 2)
    for t in thresholds:
        mask   = proba_1 >= t
        n_app  = int(mask.sum())
        if n_app == 0:
            logger.info("  %-7.2f  (no approvals)", t)
            continue
        tp_  = int((y_true[mask] == 1).sum())
        fp_  = int((y_true[mask] == 0).sum())
        fn_  = int((y_true[~mask] == 1).sum())
        tn_  = int((y_true[~mask] == 0).sum())
        prec = _div(tp_, tp_ + fp_)
        rec  = _div(tp_, tp_ + fn_)
        fpr_ = _div(fp_, fp_ + tn_)
        avg_p = float(pnl[mask].mean()) if n_app else 0.0
        tot_p = float(pnl[mask].sum())  if n_app else 0.0
        logger.info("  %-7.2f  %-10.4f  %-10.4f  %-10.4f  %-12.4f  %-12.4f  %-8d",
                    t, prec, rec, fpr_, avg_p, tot_p, n_app)

    # ── 9. CONFUSION MATRICES ────────────────────────────────────────────────
    _section("9. MATRIZES DE CONFUSAO")
    for t in [0.30, 0.40, 0.50, 0.60, 0.70]:
        logger.info("")
        y_pred = (proba_1 >= t).astype(int)
        _show_confusion(y_true, y_pred, t)

    # ── BONUS: Top 20 highest scores ─────────────────────────────────────────
    _section("BONUS — TOP 20 MAIORES SCORES")
    idx_sorted = np.argsort(proba_1)[::-1][:20]
    symbols = df["_symbol"].to_numpy() if "_symbol" in df.columns else np.array(["?"] * len(df))
    created = df["_created_at"].to_numpy() if "_created_at" in df.columns else np.array(["?"] * len(df))
    logger.info("  %-4s  %-12s  %-22s  %-8s  %-8s  %-8s",
                "rank", "symbol", "created_at", "y_true", "y_prob", "y_pred@0.50")
    for rank, i in enumerate(idx_sorted, 1):
        logger.info("  %-4d  %-12s  %-22s  %-8d  %-8.4f  %-8d",
                    rank,
                    str(symbols[i])[:12],
                    str(created[i])[:22],
                    int(y_true[i]),
                    float(proba_1[i]),
                    int(proba_1[i] >= 0.50))

    top10_wr  = float((y_true[idx_sorted[:10]]  == 1).mean())
    top20_wr  = float((y_true[idx_sorted[:20]]  == 1).mean())
    top50_idx = np.argsort(proba_1)[::-1][:50]
    top50_wr  = float((y_true[top50_idx] == 1).mean()) if len(top50_idx) >= 50 else None

    logger.info("")
    logger.info("  Win rate top-10:  %.1f%%", top10_wr * 100)
    logger.info("  Win rate top-20:  %.1f%%", top20_wr * 100)
    if top50_wr is not None:
        logger.info("  Win rate top-50:  %.1f%%", top50_wr * 100)

    _section("ANALISE CONCLUIDA")
    logger.info("  Modelo: gs://%s/models/win_fast_latest.pkl", BUCKET_NAME)
    logger.info("  Dataset: %d amostras | wins=%d (%.1f%%) | losses=%d (%.1f%%)",
                len(y_true),
                int((y_true == 1).sum()), 100 * (y_true == 1).mean(),
                int((y_true == 0).sum()), 100 * (y_true == 0).mean())
