"""Evaluation Report — Generate comprehensive model evaluation metrics.

Rewritten to use the canonical label system (is_win_fast via build_training_dataframe)
and the production feature set (FEATURE_COLUMNS from feature_extractor), aligned with
WinFastTrainer. The old DatasetBuilder path used result=="WIN" labels and a different
feature set — both incorrect for evaluating the current XGBoost model.
"""

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .feature_extractor import FEATURE_COLUMNS, build_training_dataframe

logger = logging.getLogger(__name__)

DEFAULT_THRESHOLD = 0.500


async def _load_active_model_and_threshold(db: AsyncSession) -> tuple:
    """Return (model, threshold, model_id) from the active ml_models row.

    Loads the XGBoost model from GCS via the same singleton used by
    prediction_service. Reads the calibrated decision_threshold from the DB
    (Zero Hardcode — never use 0.5 here).
    """
    from .gcs_model_loader import get_model

    try:
        model = get_model()
    except Exception as exc:
        logger.warning("Could not load model from GCS: %s", exc)
        model = None

    result = await db.execute(text("""
        SELECT id, decision_threshold
        FROM ml_models
        WHERE status = 'active'
        ORDER BY activated_at DESC
        LIMIT 1
    """))
    row = result.fetchone()
    if row:
        return model, float(row.decision_threshold), str(row.id)
    return model, DEFAULT_THRESHOLD, None


async def _load_decisions(
    db: AsyncSession,
    min_date: Optional[str],
    max_date: Optional[str],
    days_lookback: int = 90,
    include_rejected: bool = False,
) -> List[Dict[str, Any]]:
    """Query decisions_log for labeled (pnl_pct IS NOT NULL) records.

    Mirrors the job.py query but with optional date range filtering.
    """
    decision_filter = (
        "decision IN ('ALLOW', 'BLOCK')"
        if include_rejected
        else "decision = 'ALLOW'"
    )
    date_clause = ""
    params: dict = {"days": f"{days_lookback} days"}

    if min_date:
        date_clause += " AND created_at >= :min_date"
        params["min_date"] = min_date
    if max_date:
        date_clause += " AND created_at <= :max_date"
        params["max_date"] = max_date
    if not min_date:
        date_clause += " AND created_at >= NOW() - INTERVAL :days"

    sql = text(f"""
        SELECT id, symbol, created_at, metrics, score,
               pnl_pct, holding_seconds, outcome, decision
        FROM (
            SELECT DISTINCT ON (symbol, DATE(created_at))
                id, symbol, created_at, metrics, score,
                pnl_pct, holding_seconds, outcome, decision
            FROM decisions_log
            WHERE l3_pass = true
              AND {decision_filter}
              AND outcome IN ('tp', 'sl')
              AND pnl_pct IS NOT NULL
              {date_clause}
            ORDER BY symbol, DATE(created_at), created_at ASC
        ) AS deduped
        ORDER BY created_at ASC
    """)
    result = await db.execute(sql, params)
    rows = result.fetchall()
    records = []
    for r in rows:
        rec = dict(r._mapping)
        # metrics may be a dict already (asyncpg JSONB) or a JSON string
        if isinstance(rec.get("metrics"), str):
            try:
                rec["metrics"] = json.loads(rec["metrics"])
            except Exception:
                rec["metrics"] = {}
        records.append(rec)
    logger.info("Loaded %d labeled decisions for evaluation", len(records))
    return records


def _analyze_probability_buckets(
    y_pred_proba: np.ndarray,
    labels: np.ndarray,
    pnl_values: np.ndarray,
    n_buckets: int = 10,
) -> pd.DataFrame:
    """Win rate and mean PnL by probability bucket."""
    cuts = pd.cut(y_pred_proba, bins=n_buckets, labels=False, duplicates="drop")
    rows = []
    for bid in sorted(pd.Series(cuts).dropna().unique()):
        mask = cuts == bid
        if mask.sum() == 0:
            continue
        rows.append({
            "bucket": int(bid),
            "prob_min": float(y_pred_proba[mask].min()),
            "prob_max": float(y_pred_proba[mask].max()),
            "prob_mean": float(y_pred_proba[mask].mean()),
            "count": int(mask.sum()),
            "wins": int(labels[mask].sum()),
            "win_rate": float(labels[mask].mean()),
            "mean_pnl_pct": float(pnl_values[mask].mean()),
        })
    return pd.DataFrame(rows)


async def generate_evaluation_report(
    db: AsyncSession,
    min_date: Optional[str] = None,
    max_date: Optional[str] = None,
    days_lookback: int = 90,
    output_path: Optional[str] = None,
    include_rejected: bool = False,
) -> Dict[str, Any]:
    """Generate a comprehensive evaluation report for the active XGBoost model.

    Uses the canonical label system (pnl > WIN_THRESHOLD), the production feature
    set (FEATURE_COLUMNS), and the calibrated decision_threshold from ml_models.

    Args:
        db: Async database session.
        min_date: ISO date string lower bound (optional; falls back to days_lookback).
        max_date: ISO date string upper bound (optional).
        days_lookback: Days of history when min_date is not set (default 90).
        output_path: Path to save the JSON report (optional).
        include_rejected: If True, includes L3_REJECTED (decision='BLOCK') records.

    Returns:
        Dict with dataset stats, metrics, feature importance, bucket analysis.
    """
    from sklearn.metrics import (
        accuracy_score,
        confusion_matrix,
        f1_score,
        precision_score,
        recall_score,
        roc_auc_score,
    )

    logger.info("=== EvaluationReport started ===")

    # Load model + threshold
    model, threshold, model_id = await _load_active_model_and_threshold(db)

    # Load and label data
    records = await _load_decisions(
        db, min_date, max_date, days_lookback, include_rejected
    )
    if not records:
        raise ValueError("No labeled decisions found for evaluation period")

    df = build_training_dataframe(records)
    if len(df) == 0:
        raise ValueError("build_training_dataframe returned empty dataframe")

    labels = df["is_win_fast"].to_numpy(dtype=int)
    pnl_values = df["_pnl_pct"].to_numpy(dtype="float32")
    win_rate = float(labels.mean())
    n_wins = int(labels.sum())
    n_losses = int((labels == 0).sum())

    logger.info(
        "Dataset: %d samples | wins=%d losses=%d base_win_rate=%.1f%%",
        len(df), n_wins, n_losses, win_rate * 100,
    )

    # Feature matrix aligned with FEATURE_COLUMNS
    feature_cols = [c for c in FEATURE_COLUMNS if c in df.columns]
    _nan = float("nan")
    X = df[feature_cols].astype("float32").to_numpy()

    if model is None:
        logger.warning("Model unavailable — returning dataset stats only")
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "model_id": model_id,
            "error": "model_unavailable",
            "dataset": {
                "n_samples": len(df),
                "n_wins": n_wins,
                "n_losses": n_losses,
                "win_rate": win_rate,
            },
        }

    # Backwards-compat: truncate X if model was trained before macro features
    expected_features = getattr(model, "n_features_in_", None)
    if expected_features is not None and X.shape[1] > expected_features:
        logger.info(
            "Model expects %d features, vector has %d — truncating macro tail",
            expected_features, X.shape[1],
        )
        X = X[:, :expected_features]

    proba = model.predict_proba(X)[:, 1]
    y_pred = (proba >= threshold).astype(int)

    # Metrics
    if len(np.unique(labels)) >= 2:
        auc = float(roc_auc_score(labels, proba))
        precision = float(precision_score(labels, y_pred, zero_division=0))
        recall = float(recall_score(labels, y_pred, zero_division=0))
        f1 = float(f1_score(labels, y_pred, zero_division=0))
        accuracy = float(accuracy_score(labels, y_pred))
        tn, fp, fn, tp = confusion_matrix(labels, y_pred).ravel()
    else:
        logger.warning("Single-class labels — metrics defaulted to 0")
        auc = precision = recall = f1 = accuracy = 0.0
        tn = fp = fn = tp = 0

    approved_mask = y_pred == 1
    mean_pnl_approved = float(pnl_values[approved_mask].mean()) if approved_mask.sum() > 0 else 0.0
    mean_pnl_all = float(pnl_values.mean())

    # Feature importance
    feat_importance: list = []
    if hasattr(model, "feature_importances_") and expected_features is not None:
        imp_cols = feature_cols[:expected_features]
        feat_importance = sorted(
            [
                {"feature": c, "importance": float(v)}
                for c, v in zip(imp_cols, model.feature_importances_)
            ],
            key=lambda x: x["importance"],
            reverse=True,
        )

    bucket_df = _analyze_probability_buckets(proba, labels, pnl_values)

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model_id": model_id,
        "threshold_used": threshold,
        "label_system": "is_win_fast (pnl > MIN_WIN_PNL_PCT + FEE_ROUND_TRIP_PCT)",
        "dataset": {
            "n_samples": len(df),
            "n_features_used": X.shape[1],
            "n_wins": n_wins,
            "n_losses": n_losses,
            "win_rate": win_rate,
            "mean_pnl_pct": mean_pnl_all,
            "date_range": {"min": min_date, "max": max_date},
        },
        "metrics": {
            "auc": auc,
            "accuracy": accuracy,
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "mean_pnl_approved": mean_pnl_approved,
            "n_approved": int(approved_mask.sum()),
            "confusion_matrix": {
                "true_negatives": int(tn),
                "false_positives": int(fp),
                "false_negatives": int(fn),
                "true_positives": int(tp),
            },
        },
        "feature_importance": feat_importance[:20],
        "bucket_analysis": bucket_df.to_dict("records"),
    }

    logger.info("=== EvaluationReport ===")
    logger.info("Samples: %d | AUC: %.4f | Precision: %.4f | Recall: %.4f | F1: %.4f",
                len(df), auc, precision, recall, f1)
    logger.info("Threshold: %.4f | Approved: %d | Mean PnL approved: %.4f%%",
                threshold, int(approved_mask.sum()), mean_pnl_approved * 100)
    if feat_importance:
        logger.info("Top 5 features: %s",
                    [(f["feature"], round(f["importance"], 4)) for f in feat_importance[:5]])
    logger.info("========================")

    if output_path:
        import pathlib
        p = pathlib.Path(output_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(report, indent=2, default=str))
        logger.info("Report saved to %s", p)

    return report


# Legacy shim — keep old class name importable so existing callers don't break.
class EvaluationReport:
    """Compatibility wrapper around generate_evaluation_report()."""

    def __init__(self, model_path: str = ""):
        self.model_path = model_path

    async def generate_report(
        self,
        db: AsyncSession,
        min_date: Optional[str] = None,
        max_date: Optional[str] = None,
        output_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        return await generate_evaluation_report(
            db=db,
            min_date=min_date,
            max_date=max_date,
            output_path=output_path,
        )
