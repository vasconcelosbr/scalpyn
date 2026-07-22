"""Read-only Score Intelligence analytics for Profile Intelligence.

The service consumes immutable point-in-time entry snapshots only.  It does not
import ML loaders/trainers, model registries, inference, Auto-Pilot mutation
services, or any shadow writer.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import logging
import math
import os
from statistics import mean, median
from typing import Any, Iterable, Mapping, Sequence
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .profile_intelligence_contract import official_params, official_where


logger = logging.getLogger(__name__)

SCORE_FIELDS = (
    "liquidity_score",
    "market_structure_score",
    "momentum_score",
    "signal_score",
    "score",
    "alpha_score",
)
ALLOWED_SOURCES = ("L1_SPECTRUM", "L3", "L3_LAB")
CLOSED_OUTCOMES = ("TP_HIT", "SL_HIT", "TIMEOUT")
MAX_ANALYTIC_ROWS = 25_000


@dataclass(frozen=True)
class ScorePolicy:
    min_total_closed_trades: int
    min_outcome_trades: int
    min_field_coverage: float
    min_distinct_symbols: int
    min_distinct_days: int
    max_single_symbol_share: float
    max_single_day_share: float


def score_policy() -> ScorePolicy:
    return ScorePolicy(
        min_total_closed_trades=int(os.getenv("PI_SCORE_MIN_TOTAL_CLOSED_TRADES", "30")),
        min_outcome_trades=int(os.getenv("PI_SCORE_MIN_OUTCOME_TRADES", "10")),
        min_field_coverage=float(os.getenv("PI_SCORE_MIN_FIELD_COVERAGE", "0.50")),
        min_distinct_symbols=int(os.getenv("PI_SCORE_MIN_DISTINCT_SYMBOLS", "3")),
        min_distinct_days=int(os.getenv("PI_SCORE_MIN_DISTINCT_DAYS", "3")),
        max_single_symbol_share=float(os.getenv("PI_SCORE_MAX_SINGLE_SYMBOL_SHARE", "0.40")),
        max_single_day_share=float(os.getenv("PI_SCORE_MAX_SINGLE_DAY_SHARE", "0.40")),
    )


def _official_sql(alias: str) -> str:
    """Give asyncpg an explicit type for the shared textual timestamp bind."""
    return official_where(alias).replace(
        ":native_capture_start_at", "CAST(:native_capture_start_at AS TIMESTAMPTZ)"
    )


def _finite(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _quantile(values: Sequence[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * q
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _summary(values: Sequence[float]) -> dict[str, Any]:
    if not values:
        return {"n": 0, "min": None, "p25": None, "median": None, "mean": None, "p75": None, "p90": None, "max": None}
    return {
        "n": len(values),
        "min": min(values),
        "p25": _quantile(values, 0.25),
        "median": median(values),
        "mean": mean(values),
        "p75": _quantile(values, 0.75),
        "p90": _quantile(values, 0.90),
        "max": max(values),
    }


def _sample_std(values: Sequence[float]) -> float:
    if len(values) < 2:
        return 0.0
    center = mean(values)
    return math.sqrt(sum((value - center) ** 2 for value in values) / (len(values) - 1))


def _effect_size(tp: Sequence[float], sl: Sequence[float]) -> float | None:
    if len(tp) < 2 or len(sl) < 2:
        return None
    numerator = (len(tp) - 1) * _sample_std(tp) ** 2 + (len(sl) - 1) * _sample_std(sl) ** 2
    denominator = len(tp) + len(sl) - 2
    pooled = math.sqrt(numerator / denominator) if denominator > 0 else 0.0
    return (mean(tp) - mean(sl)) / pooled if pooled > 0 else None


def _auc(tp: Sequence[float], sl: Sequence[float]) -> float | None:
    """Mann-Whitney AUC; higher score is treated as TP-positive."""
    if not tp or not sl:
        return None
    labelled = sorted([(value, 1) for value in tp] + [(value, 0) for value in sl], key=lambda item: item[0])
    rank_sum = 0.0
    index = 0
    while index < len(labelled):
        end = index + 1
        while end < len(labelled) and labelled[end][0] == labelled[index][0]:
            end += 1
        average_rank = ((index + 1) + end) / 2.0
        rank_sum += average_rank * sum(label for _, label in labelled[index:end])
        index = end
    n_pos, n_neg = len(tp), len(sl)
    return (rank_sum - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg)


def _ks(tp: Sequence[float], sl: Sequence[float]) -> float | None:
    if not tp or not sl:
        return None
    values = sorted(set(tp) | set(sl))
    tp_sorted, sl_sorted = sorted(tp), sorted(sl)
    tp_index = sl_index = 0
    maximum = 0.0
    for value in values:
        while tp_index < len(tp_sorted) and tp_sorted[tp_index] <= value:
            tp_index += 1
        while sl_index < len(sl_sorted) and sl_sorted[sl_index] <= value:
            sl_index += 1
        maximum = max(maximum, abs(tp_index / len(tp_sorted) - sl_index / len(sl_sorted)))
    return maximum


def _correlation(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    if len(xs) < 2 or len(xs) != len(ys):
        return None
    x_mean, y_mean = mean(xs), mean(ys)
    numerator = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys))
    x_den = sum((x - x_mean) ** 2 for x in xs)
    y_den = sum((y - y_mean) ** 2 for y in ys)
    denominator = math.sqrt(x_den * y_den)
    return numerator / denominator if denominator > 0 else None


def _rounded(value: Any, digits: int = 6) -> Any:
    return round(value, digits) if isinstance(value, float) and math.isfinite(value) else value


def _outcome_metrics(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    counts = Counter(str(row.get("outcome")) for row in rows)
    pnls = [_finite(row.get("pnl_pct")) for row in rows]
    valid_pnls = [value for value in pnls if value is not None]
    return {
        "trades": len(rows),
        "tp": counts["TP_HIT"],
        "sl": counts["SL_HIT"],
        "timeout": counts["TIMEOUT"],
        "win_rate": counts["TP_HIT"] / len(rows) if rows else None,
        "avg_pnl_pct": mean(valid_pnls) if valid_pnls else None,
        "pnl_sum_pct": sum(valid_pnls) if valid_pnls else None,
        "avg_mae_pct": _mean_field(rows, "mae_pct"),
        "avg_mfe_pct": _mean_field(rows, "mfe_pct"),
        "avg_holding_seconds": _mean_field(rows, "holding_seconds"),
    }


def _mean_field(rows: Sequence[Mapping[str, Any]], field: str) -> float | None:
    values = [_finite(row.get(field)) for row in rows]
    present = [value for value in values if value is not None]
    return mean(present) if present else None


def score_statistics(rows: Sequence[Mapping[str, Any]], policy: ScorePolicy | None = None) -> list[dict[str, Any]]:
    policy = policy or score_policy()
    total = len(rows)
    results: list[dict[str, Any]] = []
    for field in SCORE_FIELDS:
        present_rows = [row for row in rows if _finite(row.get(field)) is not None]
        by_outcome = {
            outcome: [_finite(row.get(field)) for row in present_rows if row.get("outcome") == outcome]
            for outcome in CLOSED_OUTCOMES
        }
        tp = [value for value in by_outcome["TP_HIT"] if value is not None]
        sl = [value for value in by_outcome["SL_HIT"] if value is not None]
        timeout = [value for value in by_outcome["TIMEOUT"] if value is not None]
        pnl_pairs = [
            (_finite(row.get(field)), _finite(row.get("pnl_pct")))
            for row in present_rows
        ]
        pnl_pairs = [(x, y) for x, y in pnl_pairs if x is not None and y is not None]
        tp_summary, sl_summary, timeout_summary = _summary(tp), _summary(sl), _summary(timeout)
        coverage = len(present_rows) / total if total else 0.0
        auc = _auc(tp, sl)
        confidence = "HIGH" if len(tp) >= 50 and len(sl) >= 50 and coverage >= 0.8 else (
            "MEDIUM" if len(tp) >= policy.min_outcome_trades and len(sl) >= policy.min_outcome_trades and coverage >= policy.min_field_coverage else "LOW"
        )
        results.append({
            "score": field,
            "origin": f"features_snapshot.{field}",
            "total": total,
            "present": len(present_rows),
            "missing": total - len(present_rows),
            "coverage": coverage,
            "tp": tp_summary,
            "sl": sl_summary,
            "timeout": timeout_summary,
            "delta_mean_tp_sl": (tp_summary["mean"] - sl_summary["mean"]) if tp and sl else None,
            "delta_median_tp_sl": (tp_summary["median"] - sl_summary["median"]) if tp and sl else None,
            "standardized_effect_size": _effect_size(tp, sl),
            "auc": auc,
            "auc_discrimination": max(auc, 1 - auc) if auc is not None else None,
            "direction": "HIGHER_IS_TP" if auc is not None and auc >= 0.5 else ("LOWER_IS_TP" if auc is not None else None),
            "ks_statistic": _ks(tp, sl),
            "pnl_correlation": _correlation([x for x, _ in pnl_pairs], [y for _, y in pnl_pairs]),
            "confidence": confidence,
        })
    return [{key: _rounded(value) for key, value in result.items()} for result in results]


def threshold_metrics(rows: Sequence[Mapping[str, Any]], score: str, threshold: float) -> dict[str, Any]:
    if score not in SCORE_FIELDS:
        raise ValueError("invalid_score")
    observed = [row for row in rows if _finite(row.get(score)) is not None]
    passed = [row for row in observed if _finite(row.get(score)) >= threshold]
    baseline = _outcome_metrics(observed)
    approved = _outcome_metrics(passed)
    baseline_wr = baseline["win_rate"]
    approved_wr = approved["win_rate"]
    return {
        "score": score,
        "operator": ">=",
        "threshold": threshold,
        "observed_trades": len(observed),
        "missing": len(rows) - len(observed),
        "passed": approved,
        "eliminated_trades": len(observed) - len(passed),
        "pass_rate": len(passed) / len(observed) if observed else None,
        "volume_reduction": 1 - len(passed) / len(observed) if observed else None,
        "baseline": baseline,
        "win_rate_delta": approved_wr - baseline_wr if approved_wr is not None and baseline_wr is not None else None,
        "lift": approved_wr / baseline_wr if approved_wr is not None and baseline_wr else None,
    }


def _bucket_edges(values: Sequence[float], mode: str, current_threshold: float | None) -> list[float]:
    if not values:
        return []
    low, high = min(values), max(values)
    if low == high:
        return [low, high]
    if mode == "quantile":
        candidates = [low] + [_quantile(values, q) for q in (0.2, 0.4, 0.6, 0.8)] + [high]
    elif mode == "current_threshold" and current_threshold is not None and low < current_threshold < high:
        candidates = [low, current_threshold, high]
    else:
        width = (high - low) / 5.0
        candidates = [low + width * index for index in range(6)]
    edges: list[float] = []
    for value in candidates:
        if value is not None and (not edges or value > edges[-1]):
            edges.append(float(value))
    return edges


def distribution(rows: Sequence[Mapping[str, Any]], score: str, mode: str = "fixed", current_threshold: float | None = None) -> dict[str, Any]:
    if score not in SCORE_FIELDS:
        raise ValueError("invalid_score")
    if mode not in {"fixed", "quantile", "current_threshold"}:
        raise ValueError("invalid_bucket_mode")
    present = [row for row in rows if _finite(row.get(score)) is not None]
    values = [_finite(row.get(score)) for row in present]
    edges = _bucket_edges([value for value in values if value is not None], mode, current_threshold)
    buckets = []
    for index in range(max(len(edges) - 1, 0)):
        lower, upper = edges[index], edges[index + 1]
        bucket_rows = [
            row for row in present
            if (_finite(row.get(score)) >= lower and (_finite(row.get(score)) < upper or (index == len(edges) - 2 and _finite(row.get(score)) <= upper)))
        ]
        metrics = _outcome_metrics(bucket_rows)
        buckets.append({"lower": lower, "upper": upper, "include_upper": index == len(edges) - 2, **metrics})
    return {"score": score, "mode": mode, "buckets": buckets, "deterministic": True, "persisted": False}


def _diversity(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    symbols = Counter(str(row.get("symbol")) for row in rows if row.get("symbol"))
    days = Counter(str(row.get("created_at"))[:10] for row in rows if row.get("created_at"))
    total = len(rows)
    return {
        "distinct_symbols": len(symbols),
        "distinct_days": len(days),
        "max_single_symbol_share": max(symbols.values()) / total if symbols and total else 0.0,
        "max_single_day_share": max(days.values()) / total if days and total else 0.0,
    }


def _public_numbers(payload: Any) -> Any:
    if isinstance(payload, dict):
        return {key: _public_numbers(value) for key, value in payload.items()}
    if isinstance(payload, list):
        return [_public_numbers(value) for value in payload]
    return _rounded(payload)


class ProfileScoreIntelligenceService:
    async def _resolve_scope(
        self,
        db: AsyncSession,
        *,
        user_id: UUID,
        lookback_days: int,
        source: str | None = None,
        profile_id: UUID | None = None,
        profile_version_id: UUID | None = None,
        score_engine_version_id: UUID | None = None,
        timeframe: str | None = None,
    ) -> tuple[dict[str, Any] | None, list[dict[str, Any]], datetime]:
        if source and source not in ALLOWED_SOURCES:
            raise ValueError("invalid_source")
        cutoff = datetime.now(timezone.utc)
        params = {
            "uid": str(user_id), "source": source, "profile_id": str(profile_id) if profile_id else None,
            "profile_version_id": str(profile_version_id) if profile_version_id else None,
            "score_engine_version_id": str(score_engine_version_id) if score_engine_version_id else None,
            "timeframe": timeframe, "window_start": cutoff - timedelta(days=lookback_days), "cutoff": cutoff,
            **official_params(),
        }
        result = await db.execute(text(f"""
            SELECT st.source, st.profile_id, p.name AS profile_name, st.profile_version_id,
                   pv.version_number, st.score_engine_version_id, st.profile_config_hash,
                   st.score_engine_config_hash, st.timeframe, MIN(st.created_at) AS min_at,
                   MAX(st.created_at) AS max_at, COUNT(*)::int AS trades
              FROM shadow_trades st
              JOIN profiles p ON p.id=st.profile_id AND p.user_id=:uid
              JOIN profile_versions pv ON pv.id=st.profile_version_id AND pv.profile_id=st.profile_id
             WHERE st.user_id=:uid
               AND st.source IN ('L1_SPECTRUM','L3','L3_LAB')
               AND st.outcome IN ('TP_HIT','SL_HIT','TIMEOUT')
               AND st.created_at >= GREATEST(CAST(:window_start AS TIMESTAMPTZ), CAST(:native_capture_start_at AS TIMESTAMPTZ))
               AND st.created_at <= CAST(:cutoff AS TIMESTAMPTZ)
               AND st.profile_config_hash IS NOT NULL
               AND st.score_engine_config_hash IS NOT NULL
               AND (CAST(:source AS TEXT) IS NULL OR st.source=CAST(:source AS TEXT))
               AND (CAST(:profile_id AS UUID) IS NULL OR st.profile_id=CAST(:profile_id AS UUID))
               AND (CAST(:profile_version_id AS UUID) IS NULL OR st.profile_version_id=CAST(:profile_version_id AS UUID))
               AND (CAST(:score_engine_version_id AS UUID) IS NULL OR st.score_engine_version_id=CAST(:score_engine_version_id AS UUID))
               AND (CAST(:timeframe AS TEXT) IS NULL OR st.timeframe=CAST(:timeframe AS TEXT))
               AND {_official_sql('st')}
             GROUP BY st.source,st.profile_id,p.name,st.profile_version_id,pv.version_number,
                      st.score_engine_version_id,st.profile_config_hash,st.score_engine_config_hash,st.timeframe
             ORDER BY MAX(st.created_at) DESC, COUNT(*) DESC
             LIMIT 50
        """), params)
        scopes = [dict(row) for row in result.mappings().all()]
        return (scopes[0] if scopes else None), scopes, cutoff

    async def _rows(self, db: AsyncSession, *, user_id: UUID, scope: Mapping[str, Any], window_start: datetime, cutoff: datetime) -> tuple[list[dict[str, Any]], bool, int]:
        numeric_columns = ",\n".join(
            f"CASE WHEN jsonb_typeof(st.features_snapshot->'{field}')='number' THEN (st.features_snapshot->>'{field}')::double precision END AS {field}"
            for field in SCORE_FIELDS
        )
        params = {
            "uid": str(user_id), "source": scope["source"], "profile_id": str(scope["profile_id"]),
            "profile_version_id": str(scope["profile_version_id"]), "score_engine_version_id": str(scope["score_engine_version_id"]),
            "profile_config_hash": scope["profile_config_hash"], "score_engine_config_hash": scope["score_engine_config_hash"],
            "timeframe": scope.get("timeframe"), "window_start": window_start, "cutoff": cutoff,
            "limit": MAX_ANALYTIC_ROWS + 1, **official_params(),
        }
        result = await db.execute(text(f"""
            SELECT st.id,st.outcome,st.pnl_pct,st.mae_pct,st.mfe_pct,st.holding_seconds,
                   st.symbol,st.created_at,st.entry_timestamp,st.exit_timestamp,{numeric_columns}
              FROM shadow_trades st
             WHERE st.user_id=:uid AND st.source=:source AND st.profile_id=:profile_id
               AND st.profile_version_id=:profile_version_id
               AND st.score_engine_version_id=:score_engine_version_id
               AND st.profile_config_hash=:profile_config_hash
               AND st.score_engine_config_hash=:score_engine_config_hash
               AND st.timeframe IS NOT DISTINCT FROM :timeframe
               AND st.outcome IN ('TP_HIT','SL_HIT','TIMEOUT')
               AND st.created_at >= GREATEST(CAST(:window_start AS TIMESTAMPTZ),CAST(:native_capture_start_at AS TIMESTAMPTZ))
               AND st.created_at <= CAST(:cutoff AS TIMESTAMPTZ) AND {_official_sql('st')}
             ORDER BY st.created_at DESC LIMIT CAST(:limit AS INTEGER)
        """), params)
        raw = [dict(row) for row in result.mappings().all()]
        truncated = len(raw) > MAX_ANALYTIC_ROWS
        rows = raw[:MAX_ANALYTIC_ROWS]
        return rows, truncated, int(scope["trades"])

    async def _score_engine(self, db: AsyncSession, score_engine_version_id: UUID) -> dict[str, Any]:
        result = await db.execute(text("""
            SELECT id,parent_version_id,config_hash,rules,weights,thresholds,selected_rule_ids,status,created_at
              FROM score_engine_versions WHERE id=:id
        """), {"id": str(score_engine_version_id)})
        row = result.mappings().first()
        return dict(row) if row else {}

    async def analyze(self, db: AsyncSession, *, user_id: UUID, lookback_days: int = 30, source: str | None = None, profile_id: UUID | None = None, profile_version_id: UUID | None = None, score_engine_version_id: UUID | None = None, timeframe: str | None = None) -> dict[str, Any]:
        scope, available_scopes, cutoff = await self._resolve_scope(
            db, user_id=user_id, lookback_days=lookback_days, source=source, profile_id=profile_id,
            profile_version_id=profile_version_id, score_engine_version_id=score_engine_version_id, timeframe=timeframe,
        )
        if not scope:
            logger.info("SCORE_INTELLIGENCE_VIEWED user=%s status=EMPTY", user_id)
            return {"status": "EMPTY", "read_only": True, "association_not_causation": True, "available_scopes": []}
        window_start = cutoff - timedelta(days=lookback_days)
        rows, truncated, scoped_total = await self._rows(db, user_id=user_id, scope=scope, window_start=window_start, cutoff=cutoff)
        engine = await self._score_engine(db, scope["score_engine_version_id"])
        policy = score_policy()
        statistics = score_statistics(rows, policy)
        diversity = _diversity(rows)
        counts = Counter(row.get("outcome") for row in rows)
        gates = {
            "min_total_closed_trades": len(rows) >= policy.min_total_closed_trades,
            "min_outcome_trades": counts["TP_HIT"] >= policy.min_outcome_trades and counts["SL_HIT"] >= policy.min_outcome_trades,
            "min_distinct_symbols": diversity["distinct_symbols"] >= policy.min_distinct_symbols,
            "min_distinct_days": diversity["distinct_days"] >= policy.min_distinct_days,
            "max_single_symbol_share": diversity["max_single_symbol_share"] <= policy.max_single_symbol_share,
            "max_single_day_share": diversity["max_single_day_share"] <= policy.max_single_day_share,
        }
        status = "INSUFFICIENT_SAMPLE" if not all(gates.values()) else ("PARTIAL_COVERAGE" if not any(item["coverage"] >= policy.min_field_coverage for item in statistics) or truncated else "READY")
        threshold_rows = []
        thresholds = engine.get("thresholds") or {}
        for name, raw_threshold in thresholds.items():
            value = _finite(raw_threshold)
            if value is not None:
                threshold_rows.append({"name": name, "source": "score_engine_versions.thresholds", **threshold_metrics(rows, "score", value)})
        candidate_thresholds = []
        for item in statistics:
            field = item["score"]
            values = [_finite(row.get(field)) for row in rows]
            values = [value for value in values if value is not None]
            for value in sorted(set(filter(lambda value: value is not None, (_quantile(values, 0.50), _quantile(values, 0.60), _quantile(values, 0.70), _quantile(values, 0.80), _quantile(values, 0.90))))):
                candidate_thresholds.append(threshold_metrics(rows, field, float(value)))
        viable = [item for item in candidate_thresholds if item["passed"]["trades"] >= policy.min_total_closed_trades and item["win_rate_delta"] is not None]
        best = max(viable, key=lambda item: (item["win_rate_delta"], item["passed"]["avg_pnl_pct"] or -math.inf), default=None)
        current_score_threshold = next((item for item in threshold_rows if item["name"] == "buy"), threshold_rows[0] if threshold_rows else None)
        recommendation = None
        if best:
            recommendation = {
                "informational_only": True,
                "action": "UPDATE_SCORE_THRESHOLD" if best["score"] == "score" else "OBSERVE_ONLY",
                "score": best["score"], "current_threshold": current_score_threshold["threshold"] if current_score_threshold and best["score"] == "score" else None,
                "proposed_threshold": best["threshold"], "effect": best, "confidence": "MEDIUM" if status == "READY" else "LOW",
                "risk": "observational_association_non_causal", "missing_rate": 1 - next(item["coverage"] for item in statistics if item["score"] == best["score"]),
                "concentration": diversity, "outcomes": dict(counts),
            }
        scope_public = {key: value for key, value in scope.items() if key not in {"min_at", "max_at"}}
        scope_public.update({"effective_from": scope["min_at"], "effective_to": scope["max_at"]})
        public_scopes = [{key: value for key, value in item.items()} for item in available_scopes]
        strongest = max((item for item in statistics if item["delta_mean_tp_sl"] is not None), key=lambda item: abs(item["delta_mean_tp_sl"]), default=None)
        weakest = min((item for item in statistics if item["delta_mean_tp_sl"] is not None), key=lambda item: abs(item["delta_mean_tp_sl"]), default=None)
        response = {
            "status": status, "read_only": True, "dataset": "pi-native-point-in-time-v1", "association_not_causation": True,
            "cutoff_at": cutoff, "lookback_days": lookback_days, "scope": scope_public, "available_scopes": public_scopes,
            "closed_trades": len(rows), "scoped_total": scoped_total, "open_not_matured_excluded": True, "truncated": truncated,
            "outcomes": dict(counts), "diversity": diversity, "policy": policy.__dict__, "gates": gates,
            "outcome_metrics": _outcome_metrics(rows),
            "score_statistics": statistics, "current_thresholds": threshold_rows, "recommendation": recommendation,
            "summary": {
                "strongest_separation": strongest, "weakest_separation": weakest,
                "most_permissive_threshold": min(threshold_rows, key=lambda item: item["threshold"], default=None),
                "most_discriminatory_threshold": max(threshold_rows, key=lambda item: item["lift"] or -math.inf, default=None),
                "coverage": max((item["coverage"] for item in statistics), default=0.0),
            },
            "score_engine": engine,
        }
        logger.info("SCORE_INTELLIGENCE_VIEWED user=%s profile=%s profile_version=%s score_engine_version=%s source=%s status=%s closed=%s", user_id, scope["profile_id"], scope["profile_version_id"], scope["score_engine_version_id"], scope["source"], status, len(rows))
        return _public_numbers(response)

    async def simulate(self, db: AsyncSession, *, user_id: UUID, score: str, threshold: float, **filters: Any) -> dict[str, Any]:
        analysis = await self.analyze(db, user_id=user_id, **filters)
        if analysis.get("status") == "EMPTY":
            return analysis
        scope = analysis["scope"]
        cutoff = datetime.fromisoformat(str(analysis["cutoff_at"]))
        rows, _, _ = await self._rows(db, user_id=user_id, scope=scope, window_start=cutoff - timedelta(days=int(analysis["lookback_days"])), cutoff=cutoff)
        result = threshold_metrics(rows, score, threshold)
        current = next(
            (item for item in analysis.get("current_thresholds", []) if item.get("score") == score and item.get("name") == "buy"),
            None,
        )
        difference_vs_current = None
        if current:
            current_passed = current.get("passed") or {}
            simulated_passed = result.get("passed") or {}
            difference_vs_current = {
                "threshold_delta": threshold - float(current["threshold"]),
                "passed_trades_delta": int(simulated_passed.get("trades") or 0) - int(current_passed.get("trades") or 0),
                "win_rate_delta": _difference(simulated_passed.get("win_rate"), current_passed.get("win_rate")),
                "avg_pnl_pct_delta": _difference(simulated_passed.get("avg_pnl_pct"), current_passed.get("avg_pnl_pct")),
                "volume_reduction_delta": _difference(result.get("volume_reduction"), current.get("volume_reduction")),
            }
        logger.info("SCORE_THRESHOLD_SIMULATED user=%s profile=%s profile_version=%s score_engine_version=%s source=%s score=%s threshold=%s", user_id, scope["profile_id"], scope["profile_version_id"], scope["score_engine_version_id"], scope["source"], score, threshold)
        return _public_numbers({
            "status": analysis["status"], "read_only": True, "scope": scope,
            "simulation": result, "current_threshold": current,
            "difference_vs_current": difference_vs_current,
            "created_candidate": False, "triggered_job": False, "ml_mutated": False,
        })

    async def get_distribution(self, db: AsyncSession, *, user_id: UUID, score: str, bucket_mode: str = "fixed", **filters: Any) -> dict[str, Any]:
        analysis = await self.analyze(db, user_id=user_id, **filters)
        if analysis.get("status") == "EMPTY":
            return analysis
        scope = analysis["scope"]
        cutoff = datetime.fromisoformat(str(analysis["cutoff_at"]))
        rows, _, _ = await self._rows(db, user_id=user_id, scope=scope, window_start=cutoff - timedelta(days=int(analysis["lookback_days"])), cutoff=cutoff)
        current = next((item["threshold"] for item in analysis.get("current_thresholds", []) if item["score"] == score and item["name"] == "buy"), None)
        return _public_numbers({"status": analysis["status"], "scope": scope, "distribution": distribution(rows, score, bucket_mode, current), "read_only": True})

    async def version_comparison(self, db: AsyncSession, *, user_id: UUID, lookback_days: int = 90, source: str | None = None, profile_id: UUID | None = None, **_: Any) -> dict[str, Any]:
        _, scopes, _cutoff = await self._resolve_scope(db, user_id=user_id, lookback_days=lookback_days, source=source, profile_id=profile_id)
        unique: list[dict[str, Any]] = []
        seen: set[str] = set()
        for scope in scopes:
            key = str(scope["profile_version_id"])
            if key not in seen:
                seen.add(key); unique.append(scope)
        if len(unique) < 2:
            return {"status": "COLLECTING", "read_only": True, "versions_available": len(unique), "comparison": None}
        analyses = []
        for scope in unique[:2]:
            analyses.append(await self.analyze(db, user_id=user_id, lookback_days=lookback_days, source=scope["source"], profile_id=scope["profile_id"], profile_version_id=scope["profile_version_id"], score_engine_version_id=scope["score_engine_version_id"], timeframe=scope.get("timeframe")))
        current, previous = analyses[0], analyses[1]
        current_metrics = _outcome_metrics_from_analysis(current)
        previous_metrics = _outcome_metrics_from_analysis(previous)
        return _public_numbers({
            "status": "READY_FOR_MANUAL_REVIEW" if current["status"] == previous["status"] == "READY" else "INSUFFICIENT_SAMPLE",
            "read_only": True, "decision": None, "allowed_manual_states": ["MANUAL_KEEP","MANUAL_CONTINUE_COLLECTING","MANUAL_ROLLBACK"],
            "current": {"scope": current["scope"], "metrics": current_metrics, "score_statistics": current["score_statistics"], "thresholds": current["current_thresholds"]},
            "previous": {"scope": previous["scope"], "metrics": previous_metrics, "score_statistics": previous["score_statistics"], "thresholds": previous["current_thresholds"]},
        })


def _outcome_metrics_from_analysis(analysis: Mapping[str, Any]) -> dict[str, Any]:
    metrics = dict(analysis.get("outcome_metrics") or {})
    if metrics:
        return metrics
    outcomes = analysis.get("outcomes") or {}
    closed = int(analysis.get("closed_trades") or 0)
    return {"trades": closed, "tp": outcomes.get("TP_HIT", 0), "sl": outcomes.get("SL_HIT", 0), "timeout": outcomes.get("TIMEOUT", 0), "win_rate": outcomes.get("TP_HIT", 0) / closed if closed else None}


def _difference(left: Any, right: Any) -> float | None:
    left_value, right_value = _finite(left), _finite(right)
    if left_value is None or right_value is None:
        return None
    return left_value - right_value


profile_score_intelligence_service = ProfileScoreIntelligenceService()
