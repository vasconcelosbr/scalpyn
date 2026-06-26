"""Train and validate separated XGBoost challengers for L1 and L3.

This script is intentionally separate from ``ml_trainer.job`` because the
standard trainer can still activate a model when its promotion guards pass.
The dual-lane flow always stores models as ``candidate`` and never retires or
activates champions.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import joblib
import numpy as np
import pandas as pd
from sqlalchemy import create_engine, text

SCRIPT_PATH = Path(__file__).resolve()
ROOT = SCRIPT_PATH.parents[2] if len(SCRIPT_PATH.parents) > 2 else SCRIPT_PATH.parents[1]
for candidate in (ROOT, SCRIPT_PATH.parents[1]):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

try:
    from backend.app.ml.feature_extractor import (  # type: ignore  # noqa: E402
        FEATURE_COLUMNS,
        FEATURE_SCHEMA_VERSION,
        extract_features,
        feature_columns_hash,
    )
except ModuleNotFoundError:
    from app.ml.feature_extractor import (  # type: ignore  # noqa: E402
        FEATURE_COLUMNS,
        FEATURE_SCHEMA_VERSION,
        extract_features,
        feature_columns_hash,
    )


XGB_L1_CONTRACT = "XGB_L1_SPECTRUM_V1"
XGB_L3_CONTRACT = "XGB_L3_PROFILE_V1"
PENDING_EVIDENCE = "PENDING_EVIDENCE"
SOURCE_ENCODING = {"L1_SPECTRUM": 0, "L3": 1, "L3_LAB": 2, "L3_REJECTED": 3}
LEAKAGE_FIELDS = {
    "outcome",
    "pnl_pct",
    "net_return_pct",
    "exit_price",
    "exit_timestamp",
    "closed_at",
    "time_to_tp_minutes",
    "time_to_sl_minutes",
    "label",
    "is_win_fast",
    "final_return_pct",
}
PROFILE_FEATURES = [
    "profile_id_encoded",
    "source_encoded",
    "stable_profile_bucket",
    "profile_trade_count_prior",
    "profile_positive_count_prior",
    "profile_win_rate_prior",
    "profile_precision_rolling",
    "profile_ev_rolling",
    "profile_fpr_rolling",
]


@dataclass
class DatasetBundle:
    lane: str
    contract_id: str
    label_name: str
    train_sources: list[str]
    df: pd.DataFrame
    feature_columns: list[str]
    source_breakdown: dict[str, int]
    profile_breakdown: dict[str, int] | None
    excluded_count: int
    exclusion_reasons: dict[str, int]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _json_default(value: Any) -> Any:
    if isinstance(value, (datetime, pd.Timestamp)):
        return value.isoformat()
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    if isinstance(value, np.generic):
        v = value.item()
        if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
            return None
        return v
    return str(value)


def _json_safe_raw_model_output(value: object) -> tuple[object | None, str | None]:
    """Return (json_safe_value, repr_string) for raw model output.

    Converts NaN/inf to (None, repr) so the payload survives json.dumps(allow_nan=False).
    """
    if value is None:
        return None, None
    try:
        f = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None, repr(value)
    if math.isnan(f):
        return None, "nan"
    if math.isinf(f):
        return None, "-inf" if f < 0 else "inf"
    return f, None


def _safe_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        out = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(out) or math.isinf(out):
        return None
    return out


def stable_profile_bucket(profile_id: str | None) -> int:
    if not profile_id:
        return 9999
    return int(hashlib.md5(profile_id.encode("utf-8")).hexdigest()[:8], 16) % 9999


def derive_labels(row: dict[str, Any]) -> dict[str, int]:
    """Derive reproducible economic labels from persisted shadow outcomes."""
    pnl = _safe_float(row.get("net_return_pct"))
    if pnl is None:
        pnl = _safe_float(row.get("pnl_pct"))
    pnl = pnl if pnl is not None else 0.0

    mfe_30m = _safe_float(row.get("max_profit_first_30m"))
    if mfe_30m is None:
        mfe_30m = _safe_float(row.get("mfe_pct"))
    mfe_30m = mfe_30m if mfe_30m is not None else 0.0

    mae = _safe_float(row.get("mae_pct"))
    sl_pct = _safe_float(row.get("sl_pct_applied"))
    if sl_pct is None:
        sl_pct = _safe_float(row.get("sl_pct"))
    mae_abs = abs(mae) if mae is not None else None
    sl_abs = abs(sl_pct) if sl_pct is not None else None
    mae_controlled = mae_abs is not None and (sl_abs is None or mae_abs <= sl_abs)

    holding = _safe_float(row.get("holding_seconds"))
    fast_window = holding is not None and holding <= 1800
    outcome = row.get("outcome")
    barrier = row.get("barrier_touched")
    hit_tp = outcome == "TP_HIT" or barrier == "TP"

    return {
        "l1_mfe_30m_gte_1pct": int(mfe_30m >= 1.0),
        "l1_hit_tp_before_sl": int(hit_tp),
        "l1_directional_up_30m": int(mfe_30m > 0.0),
        "l1_ev_positive_30m": int(pnl > 0.0),
        "l3_profile_ev_positive": int(pnl > 0.0),
        "l3_profile_hit_tp_before_sl": int(hit_tp),
        "l3_profile_win_30m": int(hit_tp and fast_window),
        "l3_profile_mae_controlled": int(mae_controlled),
        "l3_profile_quality_trade": int(pnl > 0.0 and hit_tp and mae_controlled),
    }


def temporal_split(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    ordered = df.sort_values("_created_at").reset_index(drop=True)
    n = len(ordered)
    train_end = int(n * 0.60)
    val_end = int(n * 0.80)
    return (
        ordered.iloc[:train_end].copy(),
        ordered.iloc[train_end:val_end].copy(),
        ordered.iloc[val_end:].copy(),
    )


def split_summary(train: pd.DataFrame, val: pd.DataFrame, test: pd.DataFrame, label: str) -> dict[str, Any]:
    def _one(part: pd.DataFrame) -> dict[str, Any]:
        return {
            "samples": int(len(part)),
            "positive_rate": float(part[label].mean()) if len(part) else None,
            "min_created_at": str(part["_created_at"].min()) if len(part) else None,
            "max_created_at": str(part["_created_at"].max()) if len(part) else None,
        }

    return {"train": _one(train), "validation": _one(val), "test": _one(test)}


def load_shadow_rows(engine, sources: list[str], lookback_days: int, require_profile_id: bool) -> list[dict[str, Any]]:
    cutoff = _utc_now() - timedelta(days=lookback_days)
    placeholders = ", ".join(f":src_{i}" for i, _ in enumerate(sources))
    params = {f"src_{i}": src for i, src in enumerate(sources)}
    profile_clause = "AND st.profile_id IS NOT NULL" if require_profile_id else ""
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                f"""
                SELECT
                    st.id::text AS shadow_id,
                    st.symbol,
                    st.source,
                    st.pnl_pct,
                    st.net_return_pct,
                    st.holding_seconds,
                    st.outcome,
                    st.features_snapshot,
                    st.created_at,
                    st.profile_id::text AS profile_id,
                    st.profile_name,
                    st.strategy_type,
                    st.profile_status_at_entry,
                    st.max_profit_first_30m,
                    st.max_profit_pct,
                    st.mae_pct,
                    st.mfe_pct,
                    st.barrier_touched,
                    st.sl_pct,
                    st.sl_pct_applied,
                    st.tp_pct,
                    st.tp_pct_applied,
                    dl.metrics AS dl_metrics
                FROM shadow_trades st
                LEFT JOIN decisions_log dl
                    ON dl.id = st.decision_id AND dl.metrics IS NOT NULL
                WHERE st.source IN ({placeholders})
                  AND st.status = 'COMPLETED'
                  AND st.pnl_pct IS NOT NULL
                  AND st.features_snapshot IS NOT NULL
                  AND st.features_snapshot::text <> '{{}}'
                  AND st.created_at >= :cutoff
                  {profile_clause}
                ORDER BY st.created_at ASC
                """
            ),
            {**params, "cutoff": cutoff},
        ).mappings().all()
    return [dict(row) for row in rows]


def _source_breakdown(records: list[dict[str, Any]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for row in records:
        src = str(row.get("source"))
        out[src] = out.get(src, 0) + 1
    return out


def _profile_breakdown(records: list[dict[str, Any]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for row in records:
        pid = str(row.get("profile_id") or "NULL")
        out[pid] = out.get(pid, 0) + 1
    return out


def _filter_features(df: pd.DataFrame, candidates: list[str]) -> tuple[list[str], list[dict[str, Any]]]:
    kept: list[str] = []
    audit: list[dict[str, Any]] = []
    for col in candidates:
        if col in LEAKAGE_FIELDS:
            audit.append({"feature": col, "status": "excluded", "reason": "leakage_name"})
            continue
        if col not in df.columns:
            audit.append({"feature": col, "status": "excluded", "reason": "missing"})
            continue
        coverage = float(df[col].notna().mean()) if len(df) else 0.0
        nunique = int(df[col].dropna().nunique())
        if coverage < 0.30:
            audit.append({"feature": col, "status": "excluded", "reason": "low_coverage", "coverage": coverage})
            continue
        if nunique <= 1:
            audit.append({"feature": col, "status": "excluded", "reason": "zero_variance", "coverage": coverage})
            continue
        kept.append(col)
        audit.append({"feature": col, "status": "included", "reason": "point_in_time", "coverage": coverage})
    return kept, audit


def _parse_jsonb(value: Any) -> dict[str, Any]:
    if not value:
        return {}
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return {}
    return dict(value) if isinstance(value, dict) else {}


def _metrics_to_df(records: list[dict[str, Any]]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for row in records:
        features = extract_features(_parse_jsonb(row.get("features_snapshot")))
        # Phase D fallback: fill None features from decisions_log.metrics when available
        dl = _parse_jsonb(row.get("dl_metrics"))
        if dl:
            dl_features = extract_features(dl)
            for k, v in dl_features.items():
                if features.get(k) is None and v is not None:
                    features[k] = v
        features.update(derive_labels(row))
        features.update(
            {
                "_shadow_id": row.get("shadow_id"),
                "_symbol": row.get("symbol"),
                "_source": row.get("source"),
                "_created_at": row.get("created_at"),
                "_profile_id": row.get("profile_id"),
                "_pnl_pct": _safe_float(row.get("net_return_pct")) or _safe_float(row.get("pnl_pct")) or 0.0,
            }
        )
        rows.append(features)
    return pd.DataFrame(rows)


def build_xgb_l1_spectrum_dataset(records: list[dict[str, Any]]) -> tuple[DatasetBundle, list[dict[str, Any]]]:
    df = _metrics_to_df(records)
    label = "l1_mfe_30m_gte_1pct"
    feature_cols, leakage_audit = _filter_features(df, list(FEATURE_COLUMNS))
    return (
        DatasetBundle(
            lane="XGB_L1_SPECTRUM",
            contract_id=XGB_L1_CONTRACT,
            label_name=label,
            train_sources=["L1_SPECTRUM"],
            df=df,
            feature_columns=feature_cols,
            source_breakdown=_source_breakdown(records),
            profile_breakdown=None,
            excluded_count=0,
            exclusion_reasons={},
        ),
        leakage_audit,
    )


def build_xgb_l3_profile_dataset(records: list[dict[str, Any]]) -> tuple[DatasetBundle, list[dict[str, Any]]]:
    valid = [row for row in records if row.get("profile_id")]
    excluded = len(records) - len(valid)
    df = _metrics_to_df(valid)
    label = "l3_profile_ev_positive"
    if len(df):
        df = df.sort_values("_created_at").reset_index(drop=True)
        df["profile_id_encoded"] = df["_profile_id"].map(stable_profile_bucket).astype(float)
        df["stable_profile_bucket"] = df["profile_id_encoded"]
        df["source_encoded"] = df["_source"].map(lambda s: SOURCE_ENCODING.get(str(s), 999)).astype(float)
        grouped = df.groupby("_profile_id", dropna=False)
        prior_count = grouped.cumcount()
        prior_positive = grouped[label].cumsum() - df[label]
        df["profile_trade_count_prior"] = prior_count.astype(float)
        df["profile_positive_count_prior"] = prior_positive.astype(float)
        df["profile_win_rate_prior"] = np.where(prior_count > 0, prior_positive / prior_count, np.nan)
        df["profile_precision_rolling"] = grouped[label].transform(
            lambda s: s.shift().rolling(20, min_periods=3).mean()
        )
        df["profile_ev_rolling"] = grouped["_pnl_pct"].transform(
            lambda s: s.shift().rolling(20, min_periods=3).mean()
        )
        df["profile_fpr_rolling"] = grouped[label].transform(
            lambda s: (1 - s).shift().rolling(20, min_periods=3).mean()
        )
    feature_cols, leakage_audit = _filter_features(df, list(FEATURE_COLUMNS) + PROFILE_FEATURES)
    return (
        DatasetBundle(
            lane="XGB_L3_PROFILE",
            contract_id=XGB_L3_CONTRACT,
            label_name=label,
            train_sources=sorted(set(str(row.get("source")) for row in valid)),
            df=df,
            feature_columns=feature_cols,
            source_breakdown=_source_breakdown(valid),
            profile_breakdown=_profile_breakdown(valid),
            excluded_count=excluded,
            exclusion_reasons={"missing_profile_id": excluded} if excluded else {},
        ),
        leakage_audit,
    )


def _roc_auc(y_true: np.ndarray, score: np.ndarray) -> float | None:
    positives = int((y_true == 1).sum())
    negatives = int((y_true == 0).sum())
    if positives == 0 or negatives == 0:
        return None
    order = np.argsort(score)
    ranks = np.empty(len(score), dtype=float)
    sorted_scores = score[order]
    start = 0
    while start < len(score):
        end = start + 1
        while end < len(score) and sorted_scores[end] == sorted_scores[start]:
            end += 1
        avg_rank = (start + 1 + end) / 2.0
        ranks[order[start:end]] = avg_rank
        start = end
    rank_sum_pos = float(ranks[y_true == 1].sum())
    return (rank_sum_pos - positives * (positives + 1) / 2.0) / (positives * negatives)


def _pr_auc(y_true: np.ndarray, score: np.ndarray) -> float | None:
    positives = int((y_true == 1).sum())
    if positives == 0:
        return None
    order = np.argsort(-score)
    y_sorted = y_true[order]
    tp = np.cumsum(y_sorted == 1)
    fp = np.cumsum(y_sorted == 0)
    precision = tp / np.maximum(tp + fp, 1)
    recall = tp / positives
    precision = np.r_[1.0, precision]
    recall = np.r_[0.0, recall]
    return float(np.trapezoid(precision, recall))


def _classification_metrics(y_true: np.ndarray, score: np.ndarray, threshold: float) -> dict[str, Any]:
    pred = score >= threshold
    tp = int((pred & (y_true == 1)).sum())
    fp = int((pred & (y_true == 0)).sum())
    tn = int(((~pred) & (y_true == 0)).sum())
    fn = int(((~pred) & (y_true == 1)).sum())
    approved = tp + fp
    return {
        "samples": int(len(y_true)),
        "positive_rate": float(y_true.mean()) if len(y_true) else None,
        "threshold": float(threshold),
        "precision": float(tp / approved) if approved else 0.0,
        "recall": float(tp / (tp + fn)) if (tp + fn) else 0.0,
        "fpr": float(fp / (fp + tn)) if (fp + tn) else 0.0,
        "roc_auc": None if len(set(y_true.tolist())) <= 1 else float(_roc_auc(y_true, score)),
        "pr_auc": None if len(set(y_true.tolist())) <= 1 else float(_pr_auc(y_true, score)),
    }


def _threshold_sweep(y_true: np.ndarray, score: np.ndarray, pnl: np.ndarray) -> list[dict[str, Any]]:
    rows = []
    for threshold in [round(x / 100, 2) for x in range(5, 100, 5)]:
        pred = score >= threshold
        tp = int((pred & (y_true == 1)).sum())
        fp = int((pred & (y_true == 0)).sum())
        tn = int(((~pred) & (y_true == 0)).sum())
        fn = int(((~pred) & (y_true == 1)).sum())
        approved = int(pred.sum())
        precision = tp / approved if approved else 0.0
        recall = tp / (tp + fn) if (tp + fn) else 0.0
        fpr = fp / (fp + tn) if (fp + tn) else 0.0
        approved_pnl = pnl[pred]
        avg_pnl = float(approved_pnl.mean()) if len(approved_pnl) else None
        baseline = float(y_true.mean()) if len(y_true) else 0.0
        rows.append(
            {
                "threshold": threshold,
                "approved_count": approved,
                "precision": precision,
                "recall": recall,
                "fpr": fpr,
                "tp": tp,
                "fp": fp,
                "tn": tn,
                "fn": fn,
                "ev": avg_pnl,
                "avg_pnl": avg_pnl,
                "lift_vs_baseline": precision / baseline if baseline > 0 else None,
            }
        )
    return rows


def select_operational_threshold(
    sweep: list[dict[str, Any]],
    lane: str,
    baseline_precision: float,
) -> tuple[float | None, str]:
    """Return (threshold, status) applying operational gates before selection.

    Returns (None, 'NO_VALID_OPERATING_POINT') when no candidate passes the gates,
    so the caller can reject the model rather than picking an extreme threshold.
    """
    if lane.startswith("XGB_L1"):
        min_approved = 30
        min_prec = 0.0
        max_fpr = 0.20
    else:
        min_approved = 50
        min_prec = baseline_precision * 1.25 if baseline_precision > 0 else 0.0
        max_fpr = 0.20

    candidates = [
        row for row in sweep
        if (
            row["approved_count"] >= min_approved
            and row["precision"] > max(min_prec, 0.0)
            and row["recall"] > 0
            and (row["ev"] if row["ev"] is not None else -999) > 0
            and row["fpr"] <= max_fpr
        )
    ]
    if not candidates:
        return None, "NO_VALID_OPERATING_POINT"
    best = max(candidates, key=lambda r: ((r["ev"] if r["ev"] is not None else -999), r["precision"]))
    return best["threshold"], "OPERATIONAL"


def _top_buckets(y_true: np.ndarray, score: np.ndarray, pnl: np.ndarray, meta: pd.DataFrame) -> dict[str, Any]:
    order = np.argsort(-score)
    baseline = float(y_true.mean()) if len(y_true) else 0.0
    out: dict[str, Any] = {}
    for pct in (1, 5, 10, 20):
        n = max(1, int(math.ceil(len(order) * pct / 100))) if len(order) else 0
        idx = order[:n]
        positives = int(y_true[idx].sum()) if n else 0
        precision = positives / n if n else 0.0
        out[f"top_{pct}pct"] = {
            "sample_count": n,
            "positive_count": positives,
            "precision": precision,
            "lift": precision / baseline if baseline > 0 else None,
            "ev": float(pnl[idx].mean()) if n else None,
            "avg_pnl": float(pnl[idx].mean()) if n else None,
            "symbols_distinct": int(meta.iloc[idx]["_symbol"].nunique()) if n and "_symbol" in meta else 0,
            "profiles_distinct": int(meta.iloc[idx]["_profile_id"].nunique()) if n and "_profile_id" in meta else 0,
        }
    return out


def _probability_distribution(score: np.ndarray) -> dict[str, Any]:
    if len(score) == 0:
        return {"count": 0}
    return {
        "count": int(len(score)),
        "min": float(np.min(score)),
        "p01": float(np.quantile(score, 0.01)),
        "p05": float(np.quantile(score, 0.05)),
        "p50": float(np.quantile(score, 0.50)),
        "p95": float(np.quantile(score, 0.95)),
        "p99": float(np.quantile(score, 0.99)),
        "max": float(np.max(score)),
        "mean": float(np.mean(score)),
        "std": float(np.std(score)),
    }


def classify_profile_threshold(
    completed_trades_total: int,
    positive_count: int,
    approved_count: int,
    precision_test: float,
    fpr_test: float,
    ev_test: float | None,
) -> tuple[str, str]:
    """Return (status, reason) for a profile's threshold candidate.

    completed_trades_total: full dataset count for the profile (not test-split count).
    Gates: completed_trades_total>=100, positive_count>=30, approved_count>=30,
    precision>=0.50, fpr<=0.20, ev>0.
    """
    if completed_trades_total < 100:
        return "cold_start", "completed_trades_total < 100"
    if positive_count < 30:
        return "cold_start", "positive_count < 30"
    if approved_count < 30:
        return "insufficient_operating_sample", "approved_count < 30"
    if ev_test is None or ev_test <= 0:
        return "rejected", "rejected_negative_ev"
    if fpr_test > 0.20:
        return "rejected", "rejected_high_fpr"
    if precision_test < 0.50:
        return "rejected", "rejected_low_precision"
    return "approved_candidate", "passes_all_criteria"


def _profile_thresholds(
    test: pd.DataFrame,
    y_true: np.ndarray,
    score: np.ndarray,
    pnl: np.ndarray,
    full_profile_counts: dict[str, int],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if "_profile_id" not in test.columns:
        return rows
    tmp = test[["_profile_id"]].copy()
    tmp["y"] = y_true
    tmp["score"] = score
    tmp["pnl"] = pnl
    for profile_id, part in tmp.groupby("_profile_id", dropna=False):
        completed_trades_total = full_profile_counts.get(str(profile_id), 0)
        positive_count = int(part["y"].sum())
        sweep = _threshold_sweep(part["y"].to_numpy(), part["score"].to_numpy(), part["pnl"].to_numpy())
        best = max(sweep, key=lambda row: (row["ev"] if row["ev"] is not None else -999, row["precision"]))
        approved_count = best["approved_count"]
        status, reason = classify_profile_threshold(
            completed_trades_total=completed_trades_total,
            positive_count=positive_count,
            approved_count=approved_count,
            precision_test=best["precision"],
            fpr_test=best["fpr"],
            ev_test=best["ev"],
        )
        rows.append(
            {
                "profile_id": str(profile_id),
                "completed_trades_total": completed_trades_total,
                "trade_count_test": int(len(part)),
                "positive_count": positive_count,
                "base_win_rate": float(part["y"].mean()),
                "precision_test": best["precision"],
                "recall_test": best["recall"],
                "fpr_test": best["fpr"],
                "ev_test": best["ev"],
                "threshold_optimal": best["threshold"],
                "approved_count": approved_count,
                "status": status,
                "reason": reason,
            }
        )
    return rows


def _hard_negative_patterns(test: pd.DataFrame, y_true: np.ndarray, score: np.ndarray, threshold: float) -> list[dict[str, Any]]:
    fp = test[(score >= threshold) & (y_true == 0)].copy()
    if fp.empty:
        return []
    for col in ("rsi", "adx", "atr_pct", "spread_pct", "volume_spike", "vwap_distance_pct"):
        if col in fp.columns:
            fp[f"{col}_bucket"] = pd.cut(fp[col], bins=5, duplicates="drop").astype(str)
    group_cols = [c for c in ["_profile_id", "_source", "_symbol", "rsi_bucket", "adx_bucket", "atr_pct_bucket"] if c in fp.columns]
    rows = []
    for key, part in fp.groupby(group_cols, dropna=False):
        key_values = key if isinstance(key, tuple) else (key,)
        pattern = {group_cols[i]: str(key_values[i]) for i in range(len(group_cols))}
        rows.append(
            {
                "pattern": pattern,
                "fp_count": int(len(part)),
                "total_count": int(len(part)),
                "fp_rate": 1.0,
                "example_symbols": sorted(set(str(x) for x in part["_symbol"].head(5))),
                "suggested_feature_or_penalty": "candidate_only_validate_next_cycle",
                "do_not_apply_as_hard_veto_without_validation": True,
            }
        )
    return sorted(rows, key=lambda row: row["fp_count"], reverse=True)[:25]


def _xgb_params(lane: str) -> dict[str, Any]:
    raw = os.getenv(f"{lane}_XGB_PARAMS") or os.getenv("XGB_DUAL_LANE_PARAMS")
    params = json.loads(raw) if raw else {}
    params.setdefault("objective", "binary:logistic")
    params.setdefault("eval_metric", "logloss")
    params.setdefault("tree_method", "hist")
    params.setdefault("random_state", 42)
    return params


def train_lane(bundle: DatasetBundle) -> dict[str, Any]:
    import xgboost as xgb

    train, val, test = temporal_split(bundle.df)
    y_train = train[bundle.label_name].to_numpy(dtype=int)
    y_val = val[bundle.label_name].to_numpy(dtype=int)
    y_test = test[bundle.label_name].to_numpy(dtype=int)
    if len(bundle.df) < 50:
        return {"status": "blocked", "reason": "insufficient_samples", "sample_count": int(len(bundle.df))}
    if len(set(y_train.tolist())) < 2:
        return {"status": "blocked", "reason": "single_class_train_split", "split": split_summary(train, val, test, bundle.label_name)}
    params = _xgb_params(bundle.lane)
    num_boost_round = int(params.pop("num_boost_round", params.pop("n_estimators", 100)))
    if "random_state" in params and "seed" not in params:
        params["seed"] = params.pop("random_state")
    dtrain = xgb.DMatrix(
        train[bundle.feature_columns],
        label=y_train,
        feature_names=bundle.feature_columns,
        missing=np.nan,
    )
    dval = xgb.DMatrix(
        val[bundle.feature_columns],
        label=y_val,
        feature_names=bundle.feature_columns,
        missing=np.nan,
    )
    dtest = xgb.DMatrix(
        test[bundle.feature_columns],
        label=y_test,
        feature_names=bundle.feature_columns,
        missing=np.nan,
    )
    model = xgb.train(
        params,
        dtrain,
        num_boost_round=num_boost_round,
        evals=[(dval, "validation")],
        verbose_eval=False,
    )
    val_score = model.predict(dval)
    test_score = model.predict(dtest)
    val_pnl = val["_pnl_pct"].to_numpy(dtype=float)
    test_pnl = test["_pnl_pct"].to_numpy(dtype=float)
    val_sweep = _threshold_sweep(y_val, val_score, val_pnl)
    baseline_precision = float(y_train.mean()) if len(y_train) else 0.0
    threshold_global, operating_point_status = select_operational_threshold(
        val_sweep, bundle.lane, baseline_precision
    )
    # Use best-available threshold for diagnostic metrics when no operational point found
    diagnostic_threshold = threshold_global if threshold_global is not None else max(
        val_sweep,
        key=lambda row: (row["ev"] if row["ev"] is not None else -999, row["precision"]),
    )["threshold"]
    metrics = {
        "validation": _classification_metrics(y_val, val_score, diagnostic_threshold),
        "test": _classification_metrics(y_test, test_score, diagnostic_threshold),
        "split": split_summary(train, val, test, bundle.label_name),
        "threshold_sweep_validation": val_sweep,
        "threshold_sweep_test": _threshold_sweep(y_test, test_score, test_pnl),
        "top_buckets_test": _top_buckets(y_test, test_score, test_pnl, test),
        "profile_thresholds": _profile_thresholds(
            test, y_test, test_score, test_pnl,
            full_profile_counts=(
                bundle.df["_profile_id"].dropna().astype(str).value_counts().to_dict()
                if "_profile_id" in bundle.df.columns else {}
            ),
        ),
        "hard_negative_patterns_json": _hard_negative_patterns(test, y_test, test_score, diagnostic_threshold),
        "probability_distribution": _probability_distribution(test_score),
        "probability_valid": bool(np.min(test_score) >= 0.0 and np.max(test_score) <= 1.0) if len(test_score) else False,
        "threshold_global": threshold_global,
        "operating_point_status": operating_point_status,
        "promotion_gate_status": PENDING_EVIDENCE,
    }
    return {
        "status": "trained",
        "model": model,
        "metrics": metrics,
        "threshold_global": threshold_global,
        "train": train,
        "val": val,
        "test": test,
        "test_score": test_score,
    }


def _dataset_hash(df: pd.DataFrame) -> str:
    ids = sorted(str(x) for x in df.get("_shadow_id", pd.Series(dtype=str)).tolist())
    return hashlib.sha256("|".join(ids).encode("utf-8")).hexdigest()


def persist_candidate(engine, bundle: DatasetBundle, trained: dict[str, Any]) -> str:
    model_id = str(uuid4())
    now = _utc_now()
    version = f"{bundle.lane.lower()}_{now.strftime('%Y%m%d_%H%M%S')}"
    metrics = trained["metrics"]
    model_payload = {
        "model": trained["model"],
        "feature_columns": bundle.feature_columns,
        "metadata": {
            "model_lane": bundle.lane,
            "model_family": "XGBoost",
            "model_role": "challenger",
            "label_name": bundle.label_name,
            "dataset_contract_id": bundle.contract_id,
            "train_sources": bundle.train_sources,
            "promotion_gate_status": PENDING_EVIDENCE,
            "feature_schema_version": FEATURE_SCHEMA_VERSION,
        },
    }
    buf = io.BytesIO()
    joblib.dump(model_payload, buf)
    blob = buf.getvalue()
    fc_hash = feature_columns_hash(bundle.feature_columns)
    train, val, test = trained["train"], trained["val"], trained["test"]
    hyperparams = {
        "model_family": "XGBoost",
        "model_role": "challenger",
        "train_sources": bundle.train_sources,
        "source_breakdown": bundle.source_breakdown,
        "profile_breakdown": bundle.profile_breakdown,
        "threshold_by_profile_json": metrics.get("profile_thresholds", []),
        "promotion_gate_status": PENDING_EVIDENCE,
    }
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO ml_models (
                    id, version, status, hyperparams,
                    train_samples, val_samples, test_samples,
                    precision_score, recall_score, f1_score, roc_auc,
                    false_positive_rate, train_from, train_to,
                    model_path, decision_threshold, activated_at, notes,
                    feature_columns_json, feature_columns_hash, feature_count,
                    feature_schema_version, dataset_query_cutoff,
                    comparison_vs_previous, model_blob, model_scope,
                    training_scope, source_filter, dataset_hash, query_hash,
                    label_version, model_lane, dataset_contract_id,
                    metrics_json, target_window_seconds
                ) VALUES (
                    :id, :version, 'candidate', CAST(:hyperparams AS JSONB),
                    :n_train, :n_val, :n_test,
                    :precision, :recall, NULL, :roc_auc,
                    :fpr, :train_from, :train_to,
                    :model_path, :threshold, NULL, :notes,
                    CAST(:feature_columns_json AS JSONB), :feature_columns_hash, :feature_count,
                    :feature_schema_version, :dataset_query_cutoff,
                    CAST(:comparison_vs_previous AS JSONB), :model_blob, 'global',
                    :training_scope, :source_filter, :dataset_hash, :query_hash,
                    :label_version, :model_lane, :dataset_contract_id,
                    CAST(:metrics_json AS JSONB), 1800
                )
                """
            ),
            {
                "id": model_id,
                "version": version,
                "hyperparams": json.dumps(hyperparams, default=_json_default),
                "n_train": int(len(train)),
                "n_val": int(len(val)),
                "n_test": int(len(test)),
                "precision": metrics["test"].get("precision"),
                "recall": metrics["test"].get("recall"),
                "roc_auc": metrics["test"].get("roc_auc"),
                "fpr": metrics["test"].get("fpr"),
                "train_from": train["_created_at"].min() if len(train) else None,
                "train_to": test["_created_at"].max() if len(test) else None,
                "model_path": f"db://ml_models/{model_id}",
                "threshold": float(trained["threshold_global"]) if trained["threshold_global"] is not None else None,
                "notes": (
                    f"XGBoost dual-lane challenger | lane={bundle.lane} | "
                    f"label={bundle.label_name} | contract={bundle.contract_id} | "
                    f"status=candidate | promotion_gate_status=PENDING_EVIDENCE | "
                    f"operating_point_status={trained['metrics'].get('operating_point_status', 'UNKNOWN')}"
                ),
                "feature_columns_json": json.dumps(bundle.feature_columns),
                "feature_columns_hash": fc_hash,
                "feature_count": len(bundle.feature_columns),
                "feature_schema_version": FEATURE_SCHEMA_VERSION,
                "dataset_query_cutoff": now,
                "comparison_vs_previous": json.dumps({"champion_comparison": "pending_report"}, default=_json_default),
                "model_blob": blob,
                "training_scope": bundle.lane,
                "source_filter": ",".join(bundle.train_sources),
                "dataset_hash": _dataset_hash(bundle.df),
                "query_hash": hashlib.sha256(json.dumps(bundle.train_sources).encode("utf-8")).hexdigest(),
                "label_version": bundle.label_name,
                "model_lane": bundle.lane,
                "dataset_contract_id": bundle.contract_id,
                "metrics_json": json.dumps(metrics, default=_json_default),
            },
        )
        conn.execute(
            text(
                """
                INSERT INTO ml_model_registry (
                    model_id, source_ml_model_id, model_type, model_version,
                    strategy_skill, market_regime, dataset_version,
                    feature_schema_version, label_version,
                    train_start, train_end, validation_start, validation_end,
                    test_start, test_end, metrics_json, threshold, status,
                    rejection_reason, artifact_path, created_at, updated_at
                ) VALUES (
                    :model_id, :model_id, 'xgboost', :model_version,
                    'win_fast', 'all', :dataset_version,
                    :feature_schema_version, :label_version,
                    :train_start, :train_end, :validation_start, :validation_end,
                    :test_start, :test_end, CAST(:metrics_json AS JSONB), :threshold, 'candidate',
                    NULL, :artifact_path, :now, :now
                )
                """
            ),
            {
                "model_id": model_id,
                "model_version": version,
                "dataset_version": bundle.contract_id,
                "feature_schema_version": FEATURE_SCHEMA_VERSION,
                "label_version": bundle.label_name,
                "train_start": train["_created_at"].min() if len(train) else None,
                "train_end": train["_created_at"].max() if len(train) else None,
                "validation_start": val["_created_at"].min() if len(val) else None,
                "validation_end": val["_created_at"].max() if len(val) else None,
                "test_start": test["_created_at"].min() if len(test) else None,
                "test_end": test["_created_at"].max() if len(test) else None,
                "metrics_json": json.dumps(metrics, default=_json_default),
                "threshold": float(trained["threshold_global"]) if trained["threshold_global"] is not None else None,
                "artifact_path": f"db://ml_models/{model_id}",
                "now": now,
            },
        )
    return model_id


def preflight(engine) -> dict[str, Any]:
    with engine.connect() as conn:
        tx = conn.begin()
        conn.execute(text("SET TRANSACTION READ ONLY"))
        out = {
            "profiles_flags": dict(
                conn.execute(
                    text(
                        """
                        SELECT COUNT(*) FILTER (WHERE live_trading_enabled=true) AS live_enabled,
                               COUNT(*) FILTER (WHERE auto_pilot_enabled=true) AS autopilot_enabled,
                               COUNT(*) AS total_profiles
                        FROM profiles
                        """
                    )
                ).mappings().one()
            ),
            "possible_live_orders": dict(
                conn.execute(
                    text(
                        """
                        SELECT COUNT(*) AS possible_live_orders
                        FROM orders
                        WHERE status NOT IN ('cancelled','rejected','simulation','shadow')
                        """
                    )
                ).mappings().one()
            ),
        }
        tx.rollback()
    return out


def write_report(path: Path, summary: dict[str, Any]) -> None:
    lines = [
        "# RELATORIO_XGB_DUAL_LANE_LABELS_2026-06-26",
        "",
        f"- generated_at: {summary['generated_at']}",
        f"- commit_hash: {summary['commit_hash']}",
        f"- verdict: {summary['verdict']}",
        f"- persisted: {summary['persisted']}",
        "",
        "## Pre-flight",
        "```json",
        json.dumps(summary["preflight"], indent=2, default=_json_default),
        "```",
        "",
        "## Dataset Contracts",
        "```json",
        json.dumps(summary["datasets"], indent=2, default=_json_default),
        "```",
        "",
        "## Leakage Audit",
        "```json",
        json.dumps(summary["leakage_audit"], indent=2, default=_json_default),
        "```",
        "",
        "## Metrics",
        "```json",
        json.dumps(summary["metrics"], indent=2, default=_json_default),
        "```",
        "",
        "## Ledger",
        "| Affirmacao | Origem | Valor literal |",
        "|---|---|---|",
        f"| live_enabled | preflight SQL | {summary['preflight']['profiles_flags']['live_enabled']} |",
        f"| autopilot_enabled | preflight SQL | {summary['preflight']['profiles_flags']['autopilot_enabled']} |",
        f"| possible_live_orders | preflight SQL | {summary['preflight']['possible_live_orders']['possible_live_orders']} |",
        f"| L1 contract | script constant | {XGB_L1_CONTRACT} |",
        f"| L3 contract | script constant | {XGB_L3_CONTRACT} |",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def git_head() -> str:
    import subprocess

    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()


def run(args: argparse.Namespace) -> dict[str, Any]:
    db_url = os.getenv("DATABASE_PUBLIC_URL") or os.getenv("DATABASE_URL") or os.getenv("DB_URL")
    if not db_url:
        raise SystemExit("DATABASE_PUBLIC_URL, DATABASE_URL or DB_URL is required")
    engine = create_engine(db_url, pool_pre_ping=True)
    pf = preflight(engine)
    if pf["profiles_flags"]["live_enabled"] != 0 or pf["profiles_flags"]["autopilot_enabled"] != 0:
        raise SystemExit("Safety gate failed: live/autopilot enabled profiles found")
    if pf["possible_live_orders"]["possible_live_orders"] != 0:
        raise SystemExit("Safety gate failed: possible live orders found")

    l1_records = load_shadow_rows(engine, ["L1_SPECTRUM"], args.lookback_days, require_profile_id=False)
    l3_sources = [s.strip() for s in args.l3_sources.split(",") if s.strip()]
    l3_records = load_shadow_rows(engine, l3_sources, args.lookback_days, require_profile_id=True)
    l1_bundle, l1_leakage = build_xgb_l1_spectrum_dataset(l1_records)
    l3_bundle, l3_leakage = build_xgb_l3_profile_dataset(l3_records)

    trained: dict[str, Any] = {
        "l1": train_lane(l1_bundle),
        "l3": train_lane(l3_bundle),
    }
    persisted_ids: dict[str, str] = {}
    if args.persist:
        for key, bundle in (("l1", l1_bundle), ("l3", l3_bundle)):
            if trained[key]["status"] == "trained":
                persisted_ids[key] = persist_candidate(engine, bundle, trained[key])

    metrics = {
        key: {k: v for k, v in result.items() if k not in {"model", "train", "val", "test", "test_score"}}
        for key, result in trained.items()
    }
    probability_ok = all(
        result.get("metrics", {}).get("probability_valid")
        for result in trained.values()
        if result.get("status") == "trained"
    )
    both_trained = all(result.get("status") == "trained" for result in trained.values())
    both_have_operating_point = all(
        result.get("metrics", {}).get("operating_point_status") == "OPERATIONAL"
        for result in trained.values()
        if result.get("status") == "trained"
    )
    if both_trained and probability_ok and both_have_operating_point:
        verdict = "XGB_DUAL_LANE_CHALLENGERS_VALIDATED"
    elif both_trained and not both_have_operating_point:
        verdict = "XGB_DUAL_LANE_REJECTED_NO_OPERATING_POINT"
    elif any(result.get("status") == "blocked" for result in trained.values()):
        verdict = "BLOCKED_XGB_DUAL_LANE"
    else:
        verdict = "INCONCLUSIVE_XGB_DUAL_LANE_NEEDS_MORE_DATA"
    summary = {
        "generated_at": _utc_now().isoformat(),
        "commit_hash": git_head(),
        "preflight": pf,
        "persisted": bool(args.persist),
        "persisted_model_ids": persisted_ids,
        "datasets": {
            "l1": {
                "dataset_contract_id": l1_bundle.contract_id,
                "model_lane": l1_bundle.lane,
                "train_sources": l1_bundle.train_sources,
                "source_breakdown": l1_bundle.source_breakdown,
                "sample_count": int(len(l1_bundle.df)),
                "positive_rate": float(l1_bundle.df[l1_bundle.label_name].mean()) if len(l1_bundle.df) else None,
                "feature_count": len(l1_bundle.feature_columns),
                "label_name": l1_bundle.label_name,
            },
            "l3": {
                "dataset_contract_id": l3_bundle.contract_id,
                "model_lane": l3_bundle.lane,
                "train_sources": l3_bundle.train_sources,
                "source_breakdown": l3_bundle.source_breakdown,
                "profile_breakdown_count": len(l3_bundle.profile_breakdown or {}),
                "sample_count": int(len(l3_bundle.df)),
                "positive_rate": float(l3_bundle.df[l3_bundle.label_name].mean()) if len(l3_bundle.df) else None,
                "feature_count": len(l3_bundle.feature_columns),
                "label_name": l3_bundle.label_name,
                "excluded_count": l3_bundle.excluded_count,
                "exclusion_reasons": l3_bundle.exclusion_reasons,
            },
        },
        "leakage_audit": {"l1": l1_leakage, "l3": l3_leakage},
        "metrics": metrics,
        "verdict": verdict,
    }
    report_path = ROOT / "docs" / f"RELATORIO_XGB_DUAL_LANE_LABELS_{args.date}.md"
    write_report(report_path, summary)
    summary["report_path"] = str(report_path)
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lookback-days", type=int, default=int(os.getenv("XGB_DUAL_LANE_LOOKBACK_DAYS", "60")))
    parser.add_argument("--l3-sources", default=os.getenv("XGB_L3_SOURCES", "L3,L3_LAB"))
    parser.add_argument("--date", default=os.getenv("REPORT_DATE", "2026-06-26"))
    parser.add_argument("--persist", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    result = run(parse_args())
    print(json.dumps(result, indent=2, default=_json_default))




