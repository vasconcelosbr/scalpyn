"""Dataset policies, readiness gates and feature coverage audit for CatBoost L3 training.

Encapsulates the rules that prevent training CatBoost on a dataset that is
structurally broken — mixed sources with incompatible feature coverage,
regime imbalance in macro features, or insufficient profile overlap.

Exported names:
  DatasetPolicy          — string constants for the three allowed policies
  FeatureCoverageReport  — result of audit_feature_coverage()
  ReadinessReport        — result of CatBoostReadinessGate.check()
  CatBoostReadinessGate  — gate that decides whether training is safe
  audit_feature_coverage — per-feature null/zero/coverage counts
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Policy constants
# ---------------------------------------------------------------------------

class DatasetPolicy:
    L3_ONLY      = "L3_ONLY"       # source = 'L3' only
    L3_LAB_ONLY  = "L3_LAB_ONLY"   # source = 'L3_LAB' only
    L3_REJECTED_ONLY = "L3_REJECTED_ONLY"  # source = 'L3_REJECTED' only
    L3_COMBINED  = "L3_COMBINED"    # L3 + L3_LAB — blocked by default


# Source lists per policy
POLICY_SOURCES: Dict[str, List[str]] = {
    DatasetPolicy.L3_ONLY:     ["L3"],
    DatasetPolicy.L3_LAB_ONLY: ["L3_LAB"],
    DatasetPolicy.L3_REJECTED_ONLY: ["L3_REJECTED"],
    DatasetPolicy.L3_COMBINED: ["L3", "L3_LAB"],
}

# ---------------------------------------------------------------------------
# Gate thresholds (all can be overridden via constructor kwargs)
# ---------------------------------------------------------------------------

_DEFAULTS = {
    # Row counts
    "min_total_rows":        1500,
    "min_train_rows":        500,
    "min_val_rows":          250,
    "min_test_rows":         250,
    # Label quality
    "min_positive_rate":     0.10,   # 10 % floor in each split
    # Profile generalization
    "min_profile_overlap":   0.70,   # train→test overlap ≥ 70 %
    # Feature coverage
    "max_dead_feature_pct":  0.10,   # ≤ 10 % dead features allowed
    # Macro sign balance (L3 only; proportion of rows with sp500 > 0 in train)
    "min_macro_pos_pct":     0.20,   # at least 20 % positive sp500
    "min_macro_neg_pct":     0.20,   # at least 20 % negative sp500
    # Source drift gate (combined only)
    "max_source_drift_pp":   25.0,   # max percentage-point drift in source share
}


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class FeatureCoverageReport:
    source: str
    feature_name: str
    non_null_count: int
    null_count: int
    zero_count: int
    total_count: int
    coverage_pct: float          # % non-null
    zero_pct: float              # % zero among non-null
    status: str                  # FEATURE_OK | LOW_COVERAGE | DEAD_FEATURE | ALWAYS_ZERO | SOURCE_ONLY_FEATURE


@dataclass
class SplitStats:
    rows: int
    positives: int
    positive_rate: float
    profiles: int


@dataclass
class ReadinessReport:
    dataset_policy: str
    source: str
    ready: bool
    blocked_reasons: List[str] = field(default_factory=list)
    total_rows: int = 0
    train: Optional[SplitStats] = None
    val: Optional[SplitStats] = None
    test: Optional[SplitStats] = None
    profile_overlap_pct: float = 0.0
    dead_feature_pct: float = 0.0
    macro_pos_pct: Optional[float] = None
    macro_neg_pct: Optional[float] = None
    source_distribution: Dict[str, float] = field(default_factory=dict)
    feature_coverage: List[FeatureCoverageReport] = field(default_factory=list)
    recommendation: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "dataset_policy": self.dataset_policy,
            "source": self.source,
            "ready": self.ready,
            "blocked_reasons": self.blocked_reasons,
            "total_rows": self.total_rows,
            "train": _split_to_dict(self.train),
            "val": _split_to_dict(self.val),
            "test": _split_to_dict(self.test),
            "profile_overlap_pct": self.profile_overlap_pct,
            "dead_feature_pct": self.dead_feature_pct,
            "macro_pos_pct": self.macro_pos_pct,
            "macro_neg_pct": self.macro_neg_pct,
            "source_distribution": self.source_distribution,
            "feature_coverage": [
                {
                    "feature": fc.feature_name,
                    "coverage_pct": fc.coverage_pct,
                    "zero_pct": fc.zero_pct,
                    "status": fc.status,
                }
                for fc in self.feature_coverage
            ],
            "recommendation": self.recommendation,
        }


def _split_to_dict(s: Optional[SplitStats]) -> Optional[Dict[str, Any]]:
    if s is None:
        return None
    return {
        "rows": s.rows,
        "positives": s.positives,
        "positive_rate": s.positive_rate,
        "profiles": s.profiles,
    }


# ---------------------------------------------------------------------------
# Feature coverage audit
# ---------------------------------------------------------------------------

#: Features expected in L3 snapshots (technical)
L3_EXPECTED_FEATURES = [
    "rsi", "adx", "taker_ratio", "volume_delta", "macd_histogram",
    "bb_width", "atr_pct", "volume_spike", "spread_pct",
    "liquidity_score", "market_structure_score", "momentum_score",
    "signal_score", "di_trend",
    "orderbook_depth_usdt", "vwap_distance_pct", "ema9_gt_ema21",
    "ema50_gt_ema200", "volume_24h_usdt", "flow_strength",
    "trend_alignment", "rsi_slope_3", "rsi_slope_5",
    "di_plus_minus_diff", "higher_highs_5", "higher_lows_5",
]

#: Macro features — expected in L3 only when macro_context_available
L3_MACRO_FEATURES = [
    "sp500_change_1h", "nasdaq_change_1h", "russell2000_change_1h",
    "vix_value", "vix_change_1h", "dxy_value", "dxy_change_1h",
    "us10y_yield", "us10y_change_1h", "fear_greed_index",
    "macro_context_available",
]

#: Features expected in L3_LAB snapshots (no macro)
L3_LAB_EXPECTED_FEATURES = [
    "rsi", "adx", "taker_ratio", "volume_delta", "macd_histogram",
]

#: Minimum coverage to be considered alive
_COVERAGE_OK  = 0.80
_COVERAGE_LOW = 0.30


def audit_feature_coverage(
    records: List[Dict[str, Any]],
    source: str,
    feature_names: Optional[List[str]] = None,
) -> List[FeatureCoverageReport]:
    """Compute per-feature coverage statistics from raw record snapshots.

    Args:
        records:       List of shadow trade dicts (must have 'features_snapshot').
        source:        'L3' | 'L3_LAB' — determines which feature list to audit.
        feature_names: Override the default expected feature list.
    """
    if feature_names is None:
        if source == "L3":
            feature_names = L3_EXPECTED_FEATURES + L3_MACRO_FEATURES
        elif source == "L3_REJECTED":
            feature_names = L3_EXPECTED_FEATURES
        else:
            feature_names = L3_LAB_EXPECTED_FEATURES

    total = len(records)
    if total == 0:
        return []

    # Accumulate counts
    non_null: Dict[str, int] = {f: 0 for f in feature_names}
    zero: Dict[str, int] = {f: 0 for f in feature_names}

    for r in records:
        snap = r.get("features_snapshot") or {}
        if isinstance(snap, str):
            import json
            try:
                snap = json.loads(snap)
            except Exception:
                snap = {}
        for feat in feature_names:
            v = snap.get(feat)
            if v is not None:
                non_null[feat] += 1
                try:
                    if float(v) == 0.0:
                        zero[feat] += 1
                except (TypeError, ValueError):
                    pass

    reports: List[FeatureCoverageReport] = []
    for feat in feature_names:
        nn = non_null[feat]
        null_c = total - nn
        z = zero[feat]
        cov = nn / total
        zero_pct = z / nn if nn > 0 else 0.0

        if cov >= _COVERAGE_OK:
            status = "ALWAYS_ZERO" if zero_pct >= 0.95 else "FEATURE_OK"
        elif cov >= _COVERAGE_LOW:
            status = "LOW_COVERAGE"
        else:
            status = "DEAD_FEATURE"

        reports.append(FeatureCoverageReport(
            source=source,
            feature_name=feat,
            non_null_count=nn,
            null_count=null_c,
            zero_count=z,
            total_count=total,
            coverage_pct=round(100.0 * cov, 2),
            zero_pct=round(100.0 * zero_pct, 2),
            status=status,
        ))
    return reports


def dead_feature_pct(coverage: List[FeatureCoverageReport]) -> float:
    """Fraction of features classified as DEAD_FEATURE or ALWAYS_ZERO."""
    if not coverage:
        return 0.0
    dead = sum(1 for r in coverage if r.status in ("DEAD_FEATURE", "ALWAYS_ZERO"))
    return dead / len(coverage)


# ---------------------------------------------------------------------------
# CatBoostReadinessGate
# ---------------------------------------------------------------------------

class CatBoostReadinessGate:
    """Evaluate whether a given dataset policy is ready for CatBoost training.

    Usage (inside an async handler — db must be an AsyncSession):
        gate = CatBoostReadinessGate()
        report = await gate.check(db, user_id, DatasetPolicy.L3_ONLY, "is_tp_4h_v1")
        if not report.ready:
            return {"blocked": True, "reasons": report.blocked_reasons}
    """

    def __init__(self, **kwargs):
        self._cfg = {**_DEFAULTS, **kwargs}

    async def check(
        self,
        db,
        user_id,
        policy: str,
        label_version: str = "is_tp_4h_v1",
        win_threshold_s: float = 14400.0,
        lookback_days: int = 60,
    ) -> ReadinessReport:
        """Run all gates for the given policy and return a ReadinessReport."""
        from sqlalchemy import text

        sources = POLICY_SOURCES.get(policy, [])
        if not sources:
            return ReadinessReport(
                dataset_policy=policy,
                source="unknown",
                ready=False,
                blocked_reasons=["UNKNOWN_POLICY"],
                recommendation="Use L3_ONLY, L3_LAB_ONLY, L3_REJECTED_ONLY, or L3_COMBINED.",
            )

        # Combined policy: hard-blocked unless caller opts in
        if policy == DatasetPolicy.L3_COMBINED:
            return ReadinessReport(
                dataset_policy=policy,
                source="L3+L3_LAB",
                ready=False,
                blocked_reasons=["MIXED_SOURCE_DATASET_BLOCKED"],
                recommendation=(
                    "L3+L3_LAB combined training is disabled. Source composition "
                    "shift (79% L3_LAB train → 91% L3 test) caused AUC inversion "
                    "in v42. Use L3_ONLY, L3_LAB_ONLY, or L3_REJECTED_ONLY instead."
                ),
            )

        source_label = "+".join(sources)
        report = ReadinessReport(
            dataset_policy=policy,
            source=source_label,
            ready=True,
        )

        # Build source placeholders
        sp = ", ".join(f":src_{i}" for i in range(len(sources)))
        sp_params = {f"src_{i}": s for i, s in enumerate(sources)}

        from datetime import datetime, timedelta, timezone
        cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)

        # -- 1. Row counts and positive rates per split -----------------------
        rows = (await db.execute(text(f"""
            WITH eligible AS (
                SELECT
                    id, created_at, profile_id, features_snapshot, outcome, holding_seconds,
                    source,
                    CASE
                        WHEN outcome = 'TP_HIT'
                         AND holding_seconds IS NOT NULL
                         AND holding_seconds <= :win_s
                        THEN 1 ELSE 0
                    END AS is_positive,
                    ROW_NUMBER() OVER (ORDER BY created_at ASC, id ASC) AS rn,
                    COUNT(*) OVER () AS n
                FROM shadow_trades
                WHERE user_id = :uid
                  AND source IN ({sp})
                  AND profile_id IS NOT NULL
                  AND features_snapshot IS NOT NULL
                  AND features_snapshot::text <> '{{}}'
                  AND outcome IN ('TP_HIT', 'SL_HIT', 'TIMEOUT')
                  AND pnl_pct IS NOT NULL
                  AND created_at >= :cutoff
            ),
            splits AS (
                SELECT *,
                    CASE
                        WHEN rn <= FLOOR(n * 0.6) THEN 'train'
                        WHEN rn <= FLOOR(n * 0.8) THEN 'val'
                        ELSE 'test'
                    END AS split
                FROM eligible
            )
            SELECT
                split,
                COUNT(*) AS rows,
                SUM(is_positive) AS positives,
                COUNT(DISTINCT profile_id) AS profiles,
                COUNT(DISTINCT source) AS sources_present
            FROM splits
            GROUP BY split
        """), {
            "uid": str(user_id),
            "win_s": win_threshold_s,
            "cutoff": cutoff,
            **sp_params,
        })).fetchall()

        split_map: Dict[str, Dict] = {}
        for r in rows:
            split_map[r.split] = {
                "rows": r.rows,
                "positives": int(r.positives),
                "profiles": r.profiles,
                "sources_present": r.sources_present,
            }

        total_rows = sum(v["rows"] for v in split_map.values())
        report.total_rows = total_rows

        def _stats(key: str) -> Optional[SplitStats]:
            d = split_map.get(key)
            if not d:
                return None
            n = d["rows"]
            pos = d["positives"]
            return SplitStats(
                rows=n,
                positives=pos,
                positive_rate=round(pos / n, 4) if n > 0 else 0.0,
                profiles=d["profiles"],
            )

        report.train = _stats("train")
        report.val   = _stats("val")
        report.test  = _stats("test")

        # Gate: total rows
        if total_rows < self._cfg["min_total_rows"]:
            report.blocked_reasons.append(
                f"INSUFFICIENT_ROWS (have {total_rows}, need {self._cfg['min_total_rows']})"
            )

        # Gate: per-split rows
        for split_name, attr, min_key in [
            ("train", report.train, "min_train_rows"),
            ("val",   report.val,   "min_val_rows"),
            ("test",  report.test,  "min_test_rows"),
        ]:
            n = attr.rows if attr else 0
            if n < self._cfg[min_key]:
                report.blocked_reasons.append(
                    f"INSUFFICIENT_{split_name.upper()}_ROWS (have {n}, need {self._cfg[min_key]})"
                )

        # Gate: positive rate per split
        for split_name, attr in [
            ("train", report.train),
            ("val",   report.val),
            ("test",  report.test),
        ]:
            if attr is not None and attr.rows >= 20:
                if attr.positive_rate < self._cfg["min_positive_rate"]:
                    report.blocked_reasons.append(
                        f"LOW_POSITIVE_RATE_{split_name.upper()} "
                        f"({attr.positive_rate:.1%} < {self._cfg['min_positive_rate']:.0%})"
                    )

        # -- 2. Profile overlap train→test ------------------------------------
        overlap_rows = (await db.execute(text(f"""
            WITH eligible AS (
                SELECT profile_id,
                    ROW_NUMBER() OVER (ORDER BY created_at ASC, id ASC) AS rn,
                    COUNT(*) OVER () AS n
                FROM shadow_trades
                WHERE user_id = :uid
                  AND source IN ({sp})
                  AND profile_id IS NOT NULL
                  AND features_snapshot IS NOT NULL
                  AND features_snapshot::text <> '{{}}'
                  AND outcome IN ('TP_HIT', 'SL_HIT', 'TIMEOUT')
                  AND pnl_pct IS NOT NULL
                  AND created_at >= :cutoff
            ),
            splits AS (
                SELECT profile_id,
                    CASE
                        WHEN rn <= FLOOR(n * 0.6) THEN 'train'
                        WHEN rn <= FLOOR(n * 0.8) THEN 'val'
                        ELSE 'test'
                    END AS split
                FROM eligible
            ),
            train_p AS (SELECT DISTINCT profile_id FROM splits WHERE split = 'train'),
            test_p  AS (SELECT DISTINCT profile_id FROM splits WHERE split = 'test')
            SELECT
                COUNT(*) AS test_profiles,
                COUNT(*) FILTER (WHERE tp.profile_id IN (SELECT profile_id FROM train_p)) AS in_train
            FROM test_p tp
        """), {
            "uid": str(user_id), "cutoff": cutoff, **sp_params,
        })).fetchone()

        if overlap_rows and overlap_rows.test_profiles > 0:
            overlap = overlap_rows.in_train / overlap_rows.test_profiles
            report.profile_overlap_pct = round(overlap * 100, 2)
            if overlap < self._cfg["min_profile_overlap"]:
                report.blocked_reasons.append(
                    f"PROFILE_OVERLAP_LOW ({report.profile_overlap_pct:.1f}% < "
                    f"{100*self._cfg['min_profile_overlap']:.0f}%)"
                )
        else:
            report.blocked_reasons.append("NO_TEST_PROFILES")

        # -- 3. Feature coverage audit ----------------------------------------
        sample_rows = (await db.execute(text(f"""
            SELECT source, features_snapshot
            FROM shadow_trades
            WHERE user_id = :uid
              AND source IN ({sp})
              AND profile_id IS NOT NULL
              AND features_snapshot IS NOT NULL
              AND features_snapshot::text <> '{{}}'
              AND outcome IN ('TP_HIT', 'SL_HIT', 'TIMEOUT')
              AND pnl_pct IS NOT NULL
              AND created_at >= :cutoff
            ORDER BY created_at DESC
            LIMIT 500
        """), {"uid": str(user_id), "cutoff": cutoff, **sp_params})).fetchall()

        sample_records = [{"source": r.source, "features_snapshot": r.features_snapshot}
                          for r in sample_rows]

        all_coverage: List[FeatureCoverageReport] = []
        for src in sources:
            src_records = [r for r in sample_records if r["source"] == src]
            if src_records:
                cov = audit_feature_coverage(src_records, src)
                all_coverage.extend(cov)

        report.feature_coverage = all_coverage
        dfp = dead_feature_pct(all_coverage)
        report.dead_feature_pct = round(dfp * 100, 2)

        if dfp > self._cfg["max_dead_feature_pct"]:
            report.blocked_reasons.append(
                f"DEAD_FEATURES_HIGH ({report.dead_feature_pct:.1f}% > "
                f"{100*self._cfg['max_dead_feature_pct']:.0f}%)"
            )

        # -- 4. Macro sign balance (L3 only) ----------------------------------
        if "L3" in sources:
            macro_rows = (await db.execute(text(f"""
                SELECT
                    COUNT(*) FILTER (WHERE (features_snapshot->>'sp500_change_1h')::float > 0)  AS pos_sp500,
                    COUNT(*) FILTER (WHERE (features_snapshot->>'sp500_change_1h')::float < 0)  AS neg_sp500,
                    COUNT(*) FILTER (WHERE features_snapshot->>'sp500_change_1h' IS NOT NULL)    AS has_sp500,
                    COUNT(*) AS total
                FROM shadow_trades
                WHERE user_id = :uid
                  AND source = 'L3'
                  AND profile_id IS NOT NULL
                  AND features_snapshot IS NOT NULL
                  AND features_snapshot::text <> '{{}}'
                  AND outcome IN ('TP_HIT', 'SL_HIT', 'TIMEOUT')
                  AND pnl_pct IS NOT NULL
                  AND created_at >= :cutoff
                  AND rn_partition <= FLOOR(total_partition * 0.6)
            """).bindparams(uid=str(user_id), cutoff=cutoff))).fetchone()

            # Subquery form — rewrite to use CTE
            macro_rows = (await db.execute(text("""
                WITH train_l3 AS (
                    SELECT features_snapshot,
                        ROW_NUMBER() OVER (ORDER BY created_at ASC, id ASC) AS rn,
                        COUNT(*) OVER () AS n
                    FROM shadow_trades
                    WHERE user_id = :uid
                      AND source = 'L3'
                      AND profile_id IS NOT NULL
                      AND features_snapshot IS NOT NULL
                      AND features_snapshot::text <> '{}'
                      AND outcome IN ('TP_HIT','SL_HIT','TIMEOUT')
                      AND pnl_pct IS NOT NULL
                      AND created_at >= :cutoff
                )
                SELECT
                    COUNT(*) AS train_rows,
                    COUNT(*) FILTER (WHERE (features_snapshot->>'sp500_change_1h') IS NOT NULL) AS has_sp500,
                    COUNT(*) FILTER (WHERE (features_snapshot->>'sp500_change_1h')::float > 0)  AS pos_sp500,
                    COUNT(*) FILTER (WHERE (features_snapshot->>'sp500_change_1h')::float < 0)  AS neg_sp500
                FROM train_l3
                WHERE rn <= FLOOR(n * 0.6)
            """), {"uid": str(user_id), "cutoff": cutoff})).fetchone()

            if macro_rows and macro_rows.has_sp500 and macro_rows.has_sp500 > 0:
                pos_pct = macro_rows.pos_sp500 / macro_rows.has_sp500
                neg_pct = macro_rows.neg_sp500 / macro_rows.has_sp500
                report.macro_pos_pct = round(pos_pct * 100, 2)
                report.macro_neg_pct = round(neg_pct * 100, 2)
                if pos_pct < self._cfg["min_macro_pos_pct"]:
                    report.blocked_reasons.append(
                        f"MACRO_REGIME_IMBALANCE: sp500_change_1h only "
                        f"{report.macro_pos_pct:.1f}% positive in train "
                        f"(need ≥{100*self._cfg['min_macro_pos_pct']:.0f}%)"
                    )
            else:
                # No macro data at all → L3 rows have no macro context
                report.macro_pos_pct = None
                report.macro_neg_pct = None
                report.blocked_reasons.append("MACRO_DATA_ABSENT_IN_L3")

        # -- 5. Finalize ------------------------------------------------------
        report.ready = len(report.blocked_reasons) == 0
        if report.ready:
            report.recommendation = "Dataset is ready. Proceed with CatBoost training."
        else:
            report.recommendation = (
                "Do not train yet. Resolve blocked_reasons before initiating training."
            )
        return report


# ---------------------------------------------------------------------------
# Source drift check (for future combined policy validation)
# ---------------------------------------------------------------------------

def check_source_drift(
    train_source_dist: Dict[str, float],
    test_source_dist: Dict[str, float],
    max_drift_pp: float = 25.0,
) -> Tuple[bool, List[str]]:
    """Return (ok, violations) where violations lists sources with excessive drift."""
    violations = []
    all_sources = set(train_source_dist) | set(test_source_dist)
    for src in all_sources:
        train_pct = train_source_dist.get(src, 0.0)
        test_pct  = test_source_dist.get(src, 0.0)
        drift = abs(train_pct - test_pct)
        if drift > max_drift_pp:
            violations.append(
                f"{src}: train={train_pct:.1f}% test={test_pct:.1f}% "
                f"drift={drift:.1f}pp > {max_drift_pp:.0f}pp"
            )
    return (len(violations) == 0, violations)


# ---------------------------------------------------------------------------
# Governance helpers (used by API /models endpoint)
# ---------------------------------------------------------------------------

def governance_flags_for_model(model_row: Dict[str, Any]) -> Dict[str, Any]:
    """Compute governance flags for a single ml_models row.

    Returns:
        {
          "governance_warning": str | None,
          "allowed_usage": list[str],
          "blocked_reasons": list[str],
          "eligible_for_orchestrator": bool,
          "eligible_for_autopilot": bool,
          "eligible_for_allow_block": bool,
        }
    """
    warnings: List[str] = []
    blocked: List[str] = []
    allowed: List[str] = []

    status   = model_row.get("status", "")
    lane     = model_row.get("model_lane", "")
    prec     = model_row.get("precision_score")
    rec      = model_row.get("recall_score")
    test_n   = model_row.get("test_samples")
    fc_json  = model_row.get("feature_columns_json")
    metrics  = model_row.get("metrics_json") or {}
    predictive_status = model_row.get("predictive_status")
    calibration_authority = model_row.get("calibration_authority") is True
    rule_generation_authority = model_row.get("rule_generation_authority") is True

    test_auc  = (metrics.get("test") or {}).get("roc_auc")
    test_prec = (metrics.get("test") or {}).get("precision")
    test_pos  = (metrics.get("test") or {}).get("positive_rate")
    hp        = model_row.get("hyperparams") or {}
    src_bkd   = hp.get("source_breakdown") or {}
    train_src = hp.get("train_sources") or []

    is_mixed_l3 = "L3" in train_src and "L3_LAB" in train_src

    # Incomplete model (active but missing metrics)
    is_incomplete = (
        status == "active"
        and (prec is None or rec is None or test_n is None or fc_json is None)
    )
    if is_incomplete:
        blocked.append("INCOMPLETE_METRICS")

    # Candidate: if no test split, block from promotion
    if status == "candidate" and not test_n:
        blocked.append("NO_TEST_SPLIT")

    # Mixed source: block if also has bad test AUC
    if is_mixed_l3:
        blocked.append("MIXED_SOURCE_L3_L3LAB")
        if test_auc is not None and test_auc < 0.50:
            blocked.append(f"TEST_AUC_ANTI_PREDICTIVE ({test_auc:.4f})")
        if test_prec is not None and test_pos is not None and test_prec <= test_pos:
            blocked.append(
                f"NO_OPERATIONAL_EDGE (test_prec={test_prec:.3f} <= "
                f"baseline={test_pos:.3f})"
            )

    # L3_PROFILE lane with test AUC < 0.5
    if lane == "L3_PROFILE" and test_auc is not None and test_auc < 0.50:
        blocked.append(f"TEST_AUC_BELOW_RANDOM ({test_auc:.4f})")

    # Rejected-source models are diagnostic hard-negative models only. They
    # can explain what to avoid, but must never drive production ALLOW/BLOCK.
    if lane == "L3_REJECTED_PROFILE":
        blocked.append("REJECTED_SOURCE_DIAGNOSTIC_ONLY")

    if lane in {
        "L3_INTELLIGENCE",
        "L3_APPROVED_INTELLIGENCE",
        "L3_CONTEXTUAL_INTELLIGENCE",
    }:
        if predictive_status != "PREDICTIVE_APPROVED_FOR_INTELLIGENCE":
            blocked.append("PREDICTIVE_INTELLIGENCE_NOT_APPROVED")
        if not calibration_authority:
            blocked.append("CALIBRATION_AUTHORITY_DENIED")
        if not rule_generation_authority:
            blocked.append("RULE_GENERATION_AUTHORITY_DENIED")

    # Determine allowed_usage
    if blocked:
        allowed = ["ranking_shadow_only"]
        warning = "ranking_shadow_only"
    elif is_incomplete:
        allowed = ["ranking_shadow_only"]
        warning = "ranking_shadow_only"
    elif status == "candidate":
        allowed = ["ranking_shadow_only", "forward_validation_candidate"]
        warning = None
    elif status == "active":
        allowed = ["ranking_shadow_only", "forward_validation_candidate", "orchestrator_candidate"]
        warning = None
    else:
        allowed = ["ranking_shadow_only"]
        warning = None

    return {
        "governance_warning":          warning,
        "allowed_usage":               allowed,
        "blocked_reasons":             blocked,
        "eligible_for_orchestrator":   "orchestrator_candidate" in allowed,
        "eligible_for_autopilot":      False,   # never auto-promote
        "eligible_for_allow_block":    False,   # never use for ALLOW/BLOCK
    }
