"""Deterministic analytical contract for Profile Score Intelligence.

This module is deliberately independent from Anthropic.  It creates and
validates the complete evidence payload before an external model is called.
No function here writes profiles, ML datasets, model registries, or promotion
state.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
import math
from statistics import mean
from typing import Any, Callable, Mapping, Sequence


ANALYSIS_CONTRACT_VERSION = "pi-ai-analysis-v2"
ANALYSIS_SKILL_VERSION = "profile_intelligence_analysis_skill_v2"
CANONICAL_KEY_FIELDS = ("decision_id", "event_id", "ranking_id")
PENALTY_ALTERNATIVES = (0, -1, -2, -3, -5, -7, -10)
BONUS_ALTERNATIVES = (0, 1, 2, 3, 5, 7, 10)
APPROVED_SOURCES = frozenset(("L3", "L3_LAB"))
COUNTERFACTUAL_SOURCE = "L3_REJECTED"
CLOSED_OUTCOMES = frozenset(("TP_HIT", "SL_HIT", "TIMEOUT"))


AI_REPORT_SCHEMA_V2: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "analysis_contract_version": {"type": "string"},
        "analysis_skill_version": {"type": "string"},
        "executive_summary": {"type": "string"},
        "global_diagnosis": {
            "type": "array",
            "maxItems": 12,
            "items": {"type": "string"},
        },
        "profile_recommendations": {
            "type": "array",
            "maxItems": 60,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "profile_id": {"type": "string"},
                    "diagnosis": {"type": "string"},
                    "selected_candidate_ids": {
                        "type": "array",
                        "maxItems": 3,
                        "items": {"type": "string"},
                    },
                },
                "required": ("profile_id", "diagnosis", "selected_candidate_ids"),
            },
        },
        "risks": {
            "type": "array",
            "maxItems": 12,
            "items": {"type": "string"},
        },
        "safeguards": {
            "type": "array",
            "maxItems": 12,
            "items": {"type": "string"},
        },
    },
    "required": (
        "analysis_contract_version",
        "analysis_skill_version",
        "executive_summary",
        "global_diagnosis",
        "profile_recommendations",
        "risks",
        "safeguards",
    ),
}


PROFILE_INTELLIGENCE_ANALYSIS_SKILL_V2 = """
Você executa profile_intelligence_analysis_skill_v2.

O payload pi-ai-analysis-v2 já foi calculado e validado deterministicamente.
Não recalcule números, não invente coortes e não transforme associação em
causalidade. Evidência GLOBAL ou COUNTERFACTUAL não pode ser apresentada como
evidência PROFILE. Selecione somente candidate_ids fornecidos no mesmo
profile_id e somente quando validation.status for VALIDATED. Não recomende
candidate definitions sem aplicação validada. Preserve explicitamente riscos,
limitações de deduplicação e indisponibilidade de simulação.

Nunca autorize treino, aprovação ou promoção de modelo; escrita nos datasets
L1/L3; mutação de incumbent; ativação de Auto-Pilot; ou aplicação direta.
Toda mudança continua restrita a replay point-in-time e challenger shadow
versionado.

Retorne apenas JSON no schema solicitado, com:
analysis_contract_version=pi-ai-analysis-v2 e
analysis_skill_version=profile_intelligence_analysis_skill_v2.
""".strip()


class AnalysisPayloadBlocked(ValueError):
    """Raised when a hard analytical invariant prevents an AI call."""

    def __init__(self, code: str, details: Mapping[str, Any] | None = None):
        super().__init__(code)
        self.code = code
        self.details = dict(details or {})


def _json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "hex") and value.__class__.__name__ == "UUID":
        return str(value)
    if hasattr(value, "as_tuple"):
        return float(value)
    return value


def _hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(_json(value), sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def canonical_trade_key(row: Mapping[str, Any]) -> str | None:
    for field in CANONICAL_KEY_FIELDS:
        value = row.get(field)
        if value:
            return f"{field}:{value}"
    return None


def deduplicate_rows(
    rows: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Deduplicate without heuristic symbol/time joins.

    A cross-source analysis is blocked when even one row lacks a canonical
    decision/event/ranking key.  Such rows remain useful for source-local
    dashboards, but cannot safely participate in a global comparison.
    """
    seen: dict[str, dict[str, Any]] = {}
    duplicates: list[dict[str, Any]] = []
    missing_by_source: Counter[str] = Counter()
    key_field_counts: Counter[str] = Counter()
    for raw in rows:
        row = dict(raw)
        key = canonical_trade_key(row)
        if key is None:
            missing_by_source[str(row.get("source") or "UNKNOWN")] += 1
            continue
        key_field_counts[key.split(":", 1)[0]] += 1
        if key in seen:
            duplicates.append({
                "canonical_key": key,
                "kept_id": str(seen[key].get("id")),
                "dropped_id": str(row.get("id")),
                "kept_source": seen[key].get("source"),
                "dropped_source": row.get("source"),
            })
            continue
        row["canonical_trade_key"] = key
        seen[key] = row
    diagnostics = {
        "input_rows": len(rows),
        "deduplicated_rows": len(seen),
        "duplicate_rows_removed": len(duplicates),
        "missing_canonical_key_rows": sum(missing_by_source.values()),
        "missing_canonical_key_by_source": dict(sorted(missing_by_source.items())),
        "key_field_counts": dict(sorted(key_field_counts.items())),
        "duplicate_examples": duplicates[:25],
        "key_priority": list(CANONICAL_KEY_FIELDS),
        "heuristic_fallback_used": False,
    }
    return list(seen.values()), diagnostics


def cohort_metrics(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    counts = Counter(str(row.get("outcome")) for row in rows)
    total = len(rows)
    pnls = [_number(row.get("pnl_pct")) for row in rows]
    pnls = [value for value in pnls if value is not None]
    symbols = Counter(str(row.get("symbol")) for row in rows if row.get("symbol"))
    days = Counter(str(row.get("created_at"))[:10] for row in rows if row.get("created_at"))
    return {
        "closed": total,
        "tp": counts["TP_HIT"],
        "sl": counts["SL_HIT"],
        "timeout": counts["TIMEOUT"],
        "tp_rate": counts["TP_HIT"] / total if total else None,
        "sl_rate": counts["SL_HIT"] / total if total else None,
        "timeout_rate": counts["TIMEOUT"] / total if total else None,
        "avg_pnl_pct": mean(pnls) if pnls else None,
        "pnl_sum_pct": sum(pnls) if pnls else None,
        "distinct_symbols": len(symbols),
        "distinct_days": len(days),
        "max_single_symbol_share": max(symbols.values(), default=0) / total if total else 0.0,
        "max_single_day_share": max(days.values(), default=0) / total if total else 0.0,
    }


def confusion_matrix(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Treat approval as prediction-positive and eventual TP as actual-positive."""
    approved = [row for row in rows if row.get("source") in APPROVED_SOURCES]
    rejected = [row for row in rows if row.get("source") == COUNTERFACTUAL_SOURCE]
    tp = sum(row.get("outcome") == "TP_HIT" for row in approved)
    fp = sum(row.get("outcome") != "TP_HIT" for row in approved)
    fn = sum(row.get("outcome") == "TP_HIT" for row in rejected)
    tn = sum(row.get("outcome") != "TP_HIT" for row in rejected)
    return {
        "definition": {
            "prediction_positive": "source in [L3,L3_LAB]",
            "actual_positive": "outcome=TP_HIT",
            "timeout_classified_as": "negative",
        },
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "precision": tp / (tp + fp) if tp + fp else None,
        "recall": tp / (tp + fn) if tp + fn else None,
        "specificity": tn / (tn + fp) if tn + fp else None,
        "accuracy": (tp + tn) / (tp + fp + fn + tn) if tp + fp + fn + tn else None,
    }


def deterministic_split(
    rows: Sequence[Mapping[str, Any]], discovery_ratio: float = 0.70
) -> tuple[list[Mapping[str, Any]], list[Mapping[str, Any]]]:
    ordered = sorted(
        rows,
        key=lambda row: (
            str(row.get("created_at") or ""),
            str(row.get("canonical_trade_key") or row.get("id") or ""),
        ),
    )
    if len(ordered) < 2:
        return ordered, []
    boundary = max(1, min(len(ordered) - 1, int(len(ordered) * discovery_ratio)))
    return ordered[:boundary], ordered[boundary:]


def _condition_matches(
    row: Mapping[str, Any],
    bucket: Mapping[str, Any],
    features_getter: Callable[[Mapping[str, Any]], Mapping[str, Any]],
) -> bool:
    features = features_getter(row)
    value = _number(features.get(str(bucket["indicator"])))
    if value is None:
        return False
    try:
        return bool(bucket["condition"](value))
    except Exception:
        return False


def _rule_condition(bucket: Mapping[str, Any]) -> dict[str, Any]:
    if bucket.get("range_min") is not None and bucket.get("range_max") is not None:
        return {"operator": "between", "min": bucket["range_min"], "max": bucket["range_max"]}
    if bucket.get("range_min") is not None:
        return {"operator": ">=", "value": bucket["range_min"]}
    if bucket.get("range_max") is not None:
        return {"operator": "<", "value": bucket["range_max"]}
    text_value = str(bucket.get("value_text") or "")
    if text_value == "true":
        return {"operator": "==", "value": 1}
    if text_value == "false":
        return {"operator": "==", "value": 0}
    if text_value.startswith(">"):
        return {"operator": ">", "value": 0}
    return {"operator": "<=", "value": 0}


def _candidate_effect(
    rows: Sequence[Mapping[str, Any]],
    bucket: Mapping[str, Any],
    features_getter: Callable[[Mapping[str, Any]], Mapping[str, Any]],
) -> dict[str, Any]:
    present: list[Mapping[str, Any]] = []
    absent: list[Mapping[str, Any]] = []
    for row in rows:
        (present if _condition_matches(row, bucket, features_getter) else absent).append(row)
    present_metrics = cohort_metrics(present)
    absent_metrics = cohort_metrics(absent)
    delta = None
    if present_metrics["sl_rate"] is not None and absent_metrics["sl_rate"] is not None:
        delta = present_metrics["sl_rate"] - absent_metrics["sl_rate"]
    return {
        "present": present_metrics,
        "absent": absent_metrics,
        "sl_rate_delta_present_minus_absent": delta,
    }


def _score_from_row(row: Mapping[str, Any], features: Mapping[str, Any]) -> float | None:
    for key in (
        "score_total",
        "total_score",
        "robust_score",
        "final_score",
        "_score",
        "score",
    ):
        value = _number(features.get(key))
        if value is not None:
            return value
    return _number(row.get("score_total"))


def _threshold_from_config(config: Mapping[str, Any]) -> float | None:
    for path in (
        ("min_score",),
        ("score_threshold",),
        ("scoring", "min_score"),
        ("signals", "min_score"),
        ("entry_triggers", "min_score"),
    ):
        value: Any = config
        for part in path:
            if not isinstance(value, Mapping):
                value = None
                break
            value = value.get(part)
        parsed = _number(value)
        if parsed is not None:
            return parsed
    return None


def simulate_points(
    rows: Sequence[Mapping[str, Any]],
    bucket: Mapping[str, Any],
    points: Sequence[int],
    config: Mapping[str, Any],
    features_getter: Callable[[Mapping[str, Any]], Mapping[str, Any]],
) -> list[dict[str, Any]]:
    threshold = _threshold_from_config(config)
    score_coverage = sum(
        _score_from_row(row, features_getter(row)) is not None for row in rows
    )
    simulations = []
    for delta in points:
        if threshold is None or score_coverage != len(rows):
            simulations.append({
                "points": delta,
                "status": "BLOCKED_SCORE_BASELINE_UNAVAILABLE",
                "threshold": threshold,
                "score_coverage": score_coverage,
                "row_count": len(rows),
                "selected": None,
                "metrics": None,
            })
            continue
        selected = []
        for row in rows:
            features = features_getter(row)
            score = _score_from_row(row, features)
            adjusted = score + delta if _condition_matches(row, bucket, features_getter) else score
            if adjusted >= threshold:
                selected.append(row)
        simulations.append({
            "points": delta,
            "status": "SIMULATED",
            "threshold": threshold,
            "score_coverage": score_coverage,
            "row_count": len(rows),
            "selected": len(selected),
            "metrics": cohort_metrics(selected),
        })
    return simulations


def build_candidates(
    rows: Sequence[Mapping[str, Any]],
    champions: Sequence[Mapping[str, Any]],
    policy: Mapping[str, Any],
    buckets: Sequence[Mapping[str, Any]],
    features_getter: Callable[[Mapping[str, Any]], Mapping[str, Any]],
) -> tuple[
    list[dict[str, Any]],
    dict[str, Any],
    list[dict[str, Any]],
    dict[str, Any],
]:
    """Build profile-local candidates and separate global counterfactual evidence."""
    min_cases = int(policy.get("score_global_min_bucket_trades", 30))
    max_changes = int(policy.get("score_global_max_changes_per_profile", 3))
    approved_by_profile: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        if row.get("source") in APPROVED_SOURCES and row.get("profile_id"):
            approved_by_profile[str(row["profile_id"])].append(row)
    rejected = [row for row in rows if row.get("source") == COUNTERFACTUAL_SOURCE]

    counterfactual = {
        "scope": "COUNTERFACTUAL",
        "source": COUNTERFACTUAL_SOURCE,
        "baseline": cohort_metrics(rejected),
        "buckets": [],
        "profile_attribution_allowed": False,
    }
    for bucket in buckets:
        effect = _candidate_effect(rejected, bucket, features_getter)
        if effect["present"]["closed"] >= min_cases:
            counterfactual["buckets"].append({
                "bucket": bucket["bucket_label"],
                "indicator": bucket["indicator"],
                **effect,
            })

    candidates: list[dict[str, Any]] = []
    applications: list[dict[str, Any]] = []
    for champion in champions:
        profile_id = str(champion["profile_id"])
        own = approved_by_profile.get(profile_id, [])
        discovery, validation = deterministic_split(own)
        ranked = []
        for bucket in buckets:
            discovery_effect = _candidate_effect(discovery, bucket, features_getter)
            validation_effect = _candidate_effect(validation, bucket, features_getter)
            discovery_cases = discovery_effect["present"]["closed"]
            validation_cases = validation_effect["present"]["closed"]
            discovery_delta = discovery_effect["sl_rate_delta_present_minus_absent"]
            validation_delta = validation_effect["sl_rate_delta_present_minus_absent"]
            status = (
                "VALIDATED"
                if discovery_cases >= min_cases
                and validation_cases >= max(5, min_cases // 3)
                and discovery_delta is not None
                and validation_delta is not None
                and discovery_delta > 0
                and validation_delta > 0
                else "INSUFFICIENT_OR_UNSTABLE"
            )
            if status != "VALIDATED":
                continue
            definition_id = f"pi-def-{_hash({'bucket': bucket['bucket_label']})[:16]}"
            candidate_id = f"pi-v2-{profile_id[:8]}-{_hash({'bucket': bucket['bucket_label']})[:12]}"
            simulations = simulate_points(
                validation,
                bucket,
                tuple(sorted(set(PENALTY_ALTERNATIVES + BONUS_ALTERNATIVES))),
                champion.get("config") or {},
                features_getter,
            )
            rule = {
                "id": candidate_id,
                "rule_id": candidate_id,
                "indicator": bucket["indicator"],
                "bucket": bucket["bucket_label"],
                **_rule_condition(bucket),
                "points": -5,
                "category": "signal",
                "name": f"PI v2 shadow penalty: {bucket['bucket_label']}",
                "description": "Penalidade candidata validada em coorte profile-local.",
                "manual_profile_intelligence": True,
            }
            item = {
                "candidate_id": candidate_id,
                "candidate_definition_id": definition_id,
                "scope": "PROFILE",
                "profile_id": profile_id,
                "profile_name": champion.get("profile_name"),
                "action_type": "ADD_SCORE_PENALTY",
                "target_path": "/scoring/generated_rules",
                "current_value": None,
                "proposed_value": rule,
                "discovery": discovery_effect,
                "validation": {"status": status, **validation_effect},
                "simulations": simulations,
                "sources": sorted({str(row.get("source")) for row in own}),
            }
            ranked.append(item)
        ranked.sort(
            key=lambda item: (
                item["validation"]["sl_rate_delta_present_minus_absent"] or 0,
                item["validation"]["present"]["closed"],
            ),
            reverse=True,
        )
        selected = ranked[:max_changes]
        candidates.extend(selected)
        applications.extend({
            "candidate_id": item["candidate_id"],
            "candidate_definition_id": item["candidate_definition_id"],
            "profile_id": profile_id,
        } for item in selected)

    definitions = {item["candidate_definition_id"] for item in candidates}
    mutation_instances = sum(
        1 for item in candidates for sim in item["simulations"]
        if sim["points"] != 0 and sim["status"] == "SIMULATED"
    )
    summary = {
        "candidate_definitions": len(definitions),
        "profile_rule_applications": len(applications),
        "mutation_instances": mutation_instances,
        "penalty_alternatives": list(PENALTY_ALTERNATIVES),
        "bonus_alternatives": list(BONUS_ALTERNATIVES),
    }
    return candidates, summary, applications, counterfactual


def build_overlap_analysis(
    rows: Sequence[Mapping[str, Any]],
    candidates: Sequence[Mapping[str, Any]],
    buckets_by_label: Mapping[str, Mapping[str, Any]],
    features_getter: Callable[[Mapping[str, Any]], Mapping[str, Any]],
) -> list[dict[str, Any]]:
    by_profile: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for item in candidates:
        by_profile[str(item["profile_id"])].append(item)
    rows_by_profile: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        if row.get("source") in APPROVED_SOURCES and row.get("profile_id"):
            rows_by_profile[str(row["profile_id"])].append(row)
    output = []
    for profile_id, items in by_profile.items():
        for index, left in enumerate(items):
            for right in items[index + 1:]:
                left_bucket = buckets_by_label[left["proposed_value"]["bucket"]]
                right_bucket = buckets_by_label[right["proposed_value"]["bucket"]]
                groups: dict[str, list[Mapping[str, Any]]] = {
                    "a_only": [], "b_only": [], "and": [], "or": [],
                }
                for row in rows_by_profile.get(profile_id, []):
                    a = _condition_matches(row, left_bucket, features_getter)
                    b = _condition_matches(row, right_bucket, features_getter)
                    if a and not b:
                        groups["a_only"].append(row)
                    if b and not a:
                        groups["b_only"].append(row)
                    if a and b:
                        groups["and"].append(row)
                    if a or b:
                        groups["or"].append(row)
                output.append({
                    "profile_id": profile_id,
                    "candidate_a": left["candidate_id"],
                    "candidate_b": right["candidate_id"],
                    "cohorts": {key: cohort_metrics(value) for key, value in groups.items()},
                    "combined_penalty_cap": -10,
                })
    return output


def validate_analysis_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    if payload.get("analysis_contract_version") != ANALYSIS_CONTRACT_VERSION:
        errors.append("INVALID_ANALYSIS_CONTRACT_VERSION")
    if payload.get("analysis_skill_version") != ANALYSIS_SKILL_VERSION:
        errors.append("INVALID_ANALYSIS_SKILL_VERSION")
    dedup = payload.get("deduplication") or {}
    if int(dedup.get("missing_canonical_key_rows") or 0) > 0:
        errors.append("BLOCKED_CROSS_SOURCE_DEDUP_UNAVAILABLE")
    if payload.get("truncated"):
        errors.append("BLOCKED_ANALYSIS_TRUNCATED")
    for candidate in payload.get("candidates") or []:
        if candidate.get("scope") != "PROFILE":
            errors.append("INVALID_CANDIDATE_SCOPE")
        if (candidate.get("validation") or {}).get("status") != "VALIDATED":
            errors.append("UNVALIDATED_CANDIDATE_EXPOSED")
        if COUNTERFACTUAL_SOURCE in set(candidate.get("sources") or []):
            errors.append("COUNTERFACTUAL_EVIDENCE_ATTRIBUTED_TO_PROFILE")
    source_metrics = payload.get("source_metrics") or {}
    for source, metrics in source_metrics.items():
        closed = int((metrics or {}).get("closed") or 0)
        components = sum(int((metrics or {}).get(key) or 0) for key in ("tp", "sl", "timeout"))
        if closed != components:
            errors.append(f"SOURCE_METRICS_NOT_EXHAUSTIVE:{source}")
    matrix = payload.get("confusion_matrix") or {}
    if any(int(matrix.get(key) or 0) < 0 for key in ("tp", "fp", "fn", "tn")):
        errors.append("INVALID_CONFUSION_MATRIX")
    if not payload.get("candidates"):
        warnings.append("NO_VALIDATED_PROFILE_CANDIDATES")
    return {
        "valid": not errors,
        "hard_errors": sorted(set(errors)),
        "warnings": sorted(set(warnings)),
        "validated_at": datetime.now(timezone.utc).isoformat(),
    }


def validate_ai_response_against_payload(
    response: Mapping[str, Any],
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    if response.get("analysis_contract_version") != ANALYSIS_CONTRACT_VERSION:
        raise ValueError("ai_analysis_contract_mismatch")
    if response.get("analysis_skill_version") != ANALYSIS_SKILL_VERSION:
        raise ValueError("ai_analysis_skill_mismatch")
    allowed_by_profile: dict[str, set[str]] = defaultdict(set)
    for candidate in payload.get("candidates") or []:
        allowed_by_profile[str(candidate["profile_id"])].add(str(candidate["candidate_id"]))
    selected: set[str] = set()
    bounded = []
    for item in list(response.get("profile_recommendations") or [])[:60]:
        profile_id = str(item.get("profile_id") or "")
        candidate_ids = list(dict.fromkeys(
            str(value) for value in item.get("selected_candidate_ids") or []
        ))[:3]
        if any(candidate_id not in allowed_by_profile.get(profile_id, set()) for candidate_id in candidate_ids):
            raise ValueError("ai_selected_unknown_or_cross_profile_candidate")
        selected.update(candidate_ids)
        bounded.append({
            "profile_id": profile_id,
            "diagnosis": str(item.get("diagnosis") or ""),
            "selected_candidate_ids": candidate_ids,
        })
    clean = {
        "analysis_contract_version": ANALYSIS_CONTRACT_VERSION,
        "analysis_skill_version": ANALYSIS_SKILL_VERSION,
        "executive_summary": str(response.get("executive_summary") or "").strip(),
        "global_diagnosis": [str(value) for value in list(response.get("global_diagnosis") or [])[:12]],
        "profile_recommendations": bounded,
        "risks": [str(value) for value in list(response.get("risks") or [])[:12]],
        "safeguards": [str(value) for value in list(response.get("safeguards") or [])[:12]],
        "selected_candidate_ids": sorted(selected),
    }
    if not clean["executive_summary"]:
        raise ValueError("invalid_profile_score_ai_summary")
    return clean
