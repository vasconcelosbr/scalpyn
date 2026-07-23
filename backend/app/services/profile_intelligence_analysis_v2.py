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
import re
from statistics import mean
from typing import Any, Callable, Mapping, Sequence


ANALYSIS_CONTRACT_VERSION = "pi-ai-analysis-v2"
ANALYSIS_SKILL_VERSION = "profile_intelligence_analysis_skill_v3"
AI_REPORT_SCHEMA_VERSION = "pi-technical-report-v1"
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
        "report_schema_version": {"type": "string"},
        "executive_summary": {
            "type": "array",
            "description": "Entre 4 e 8 conclusões; o guard pós-IA aplica os limites.",
            "items": {"type": "string"},
        },
        "data_quality": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "integrity_assessment": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "limitations": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
            "required": ("integrity_assessment", "limitations"),
        },
        "cohort_analysis": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "l3": {"type": "array", "items": {"type": "string"}},
                "l3_lab": {"type": "array", "items": {"type": "string"}},
                "approved_combined": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "l3_rejected": {"type": "array", "items": {"type": "string"}},
            },
            "required": ("l3", "l3_lab", "approved_combined", "l3_rejected"),
        },
        "confusion_matrix_analysis": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "interpretation": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "operational_impact": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
            "required": ("interpretation", "operational_impact"),
        },
        "profile_recommendations": {
            "type": "array",
            "description": "No máximo 60 itens; o guard pós-IA aplica o limite.",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "profile_id": {"type": "string"},
                    "technical_reading": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "limitations": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "recommendation": {"type": "string"},
                    "confidence": {"type": "string"},
                    "priority": {"type": "string"},
                    "selected_candidate_ids": {
                        "type": "array",
                        "description": "No máximo 3 IDs; o guard pós-IA aplica o limite.",
                        "items": {"type": "string"},
                    },
                },
                "required": (
                    "profile_id",
                    "technical_reading",
                    "limitations",
                    "recommendation",
                    "confidence",
                    "priority",
                    "selected_candidate_ids",
                ),
            },
        },
        "redundancy_analysis": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "profile_id": {"type": "string"},
                    "candidate_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "diagnosis": {"type": "string"},
                    "recommendation": {"type": "string"},
                },
                "required": (
                    "profile_id",
                    "candidate_ids",
                    "diagnosis",
                    "recommendation",
                ),
            },
        },
        "prioritization": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "high": {"type": "array", "items": {"type": "string"}},
                "medium": {"type": "array", "items": {"type": "string"}},
                "low": {"type": "array", "items": {"type": "string"}},
                "rationale": {"type": "array", "items": {"type": "string"}},
            },
            "required": ("high", "medium", "low", "rationale"),
        },
        "next_steps": {"type": "array", "items": {"type": "string"}},
    },
    "required": (
        "analysis_contract_version",
        "analysis_skill_version",
        "report_schema_version",
        "executive_summary",
        "data_quality",
        "cohort_analysis",
        "confusion_matrix_analysis",
        "profile_recommendations",
        "redundancy_analysis",
        "prioritization",
        "next_steps",
    ),
}


PROFILE_INTELLIGENCE_ANALYSIS_SKILL_V2 = """
Você executa profile_intelligence_analysis_skill_v3 como revisor técnico sênior.

O payload pi-ai-analysis-v2 já foi calculado e validado deterministicamente.
Produza um relatório executivo claro, gramaticalmente correto, preciso,
rastreável e sem caracteres corrompidos. Preserve IDs, métricas, datas,
thresholds, status, fontes e demais valores do payload. Nunca invente, estime ou
complete um valor ausente. Se um trecho não puder ser sustentado pelo payload,
registre a limitação sem reconstrução especulativa.

Não recalcule números e não transforme associação em causalidade. Taxa de TP
acima da metade não prova lucratividade; PnL deve ser interpretado
separadamente. Na matriz de confusão, explique precisão, recall, especificidade,
taxas de falsos positivos e falsos negativos com a definição fornecida. Um
delta sl_rate_present_minus_absent positivo indica maior ocorrência de SL
quando a condição está presente, mas continua sendo evidência observacional.

Analise L3, L3_LAB e a coorte aprovada combinada separadamente. L3_REJECTED é
contrafactual e só pode ser atribuído a profile quando
profile_attribution_allowed=true. Evidência GLOBAL ou COUNTERFACTUAL nunca pode
ser apresentada como evidência PROFILE.

Analise todos os profiles presentes em candidates, mesmo quando nenhuma
mudança for selecionada. Selecione somente candidate_ids fornecidos no mesmo
profile_id e somente quando validation.status=VALIDATED. Considere consistência
entre discovery e validation, suficiência de amostra, concentração, simulação,
redundância e conflito. Use confidence apenas como ALTA, MEDIA ou BAIXA e
priority apenas como ALTA, MEDIA ou BAIXA.

Seja conciso para cobrir todos os profiles sem truncamento: cada conclusão do
resumo deve ter uma frase; cada campo de data_quality, cohort_analysis e
confusion_matrix_analysis deve ter no máximo três itens; cada profile deve ter
um ou dois itens em technical_reading, exatamente um item em limitations e uma
recomendação de uma frase. Inclua redundancy_analysis somente quando houver
overlap material no payload. next_steps deve ter entre quatro e oito itens.

Os números e tabelas verificadas serão incorporados deterministicamente pelo
sistema. Na narrativa, cite um número somente quando ele existir literalmente
no payload (percentuais podem apenas converter taxa 0..1 para 0..100). Não
substitua letras, sílabas ou acentos para remover números. Nunca produza
fragmentos como "terminado", "erminado", "texto", "exto" ou tabulações dentro
de palavras.

Nunca autorize treino, aprovação ou promoção de modelo; escrita nos datasets
L1/L3; mutação de incumbent; ativação de Auto-Pilot; ou aplicação direta.
Toda mudança continua restrita a replay point-in-time e challenger shadow
versionado.

Retorne apenas JSON no schema solicitado, com:
analysis_contract_version=pi-ai-analysis-v2 e
analysis_skill_version=profile_intelligence_analysis_skill_v3 e
report_schema_version=pi-technical-report-v1.
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
    duplicates_by_source_pair: Counter[str] = Counter()
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
            source_pair = " -> ".join(
                sorted(
                    (
                        str(seen[key].get("source") or "UNKNOWN"),
                        str(row.get("source") or "UNKNOWN"),
                    )
                )
            )
            duplicates_by_source_pair[source_pair] += 1
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
        "duplicates_by_source_pair": dict(sorted(duplicates_by_source_pair.items())),
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
        "other_outcomes": total
        - counts["TP_HIT"]
        - counts["SL_HIT"]
        - counts["TIMEOUT"],
        "outcome_counts": dict(sorted(counts.items())),
        "tp_rate": counts["TP_HIT"] / total if total else None,
        "sl_rate": counts["SL_HIT"] / total if total else None,
        "timeout_rate": counts["TIMEOUT"] / total if total else None,
        "avg_pnl_pct": mean(pnls) if pnls else None,
        "pnl_sum_pct": sum(pnls) if pnls else None,
        "pnl_n": len(pnls),
        "rate_basis": {
            "tp_rate": {"numerator": counts["TP_HIT"], "denominator": total},
            "sl_rate": {"numerator": counts["SL_HIT"], "denominator": total},
            "timeout_rate": {"numerator": counts["TIMEOUT"], "denominator": total},
        },
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
        "false_positive_rate": fp / (fp + tn) if fp + tn else None,
        "false_negative_rate": fn / (fn + tp) if fn + tp else None,
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


def cohort_period(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    timestamps = sorted(
        str(row.get("created_at"))
        for row in rows
        if row.get("created_at") is not None
    )
    return {
        "from": timestamps[0] if timestamps else None,
        "to": timestamps[-1] if timestamps else None,
        "rows": len(rows),
    }


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
    score_fields = {
        "score",
        "alpha_score",
        "total_score",
        "score_total",
        "robust_score",
        "final_score",
    }
    for section in ("signals", "entry_triggers"):
        conditions = (config.get(section) or {}).get("conditions") or []
        thresholds: set[float] = set()
        for condition in conditions:
            if not isinstance(condition, Mapping):
                continue
            field = str(
                condition.get("field") or condition.get("indicator") or ""
            ).strip().lower().replace(" ", "_")
            operator = str(condition.get("operator") or "")
            value = _number(condition.get("value"))
            if field in score_fields and operator in {">", ">="} and value is not None:
                thresholds.add(value)
        if len(thresholds) == 1:
            return next(iter(thresholds))
        if len(thresholds) > 1:
            return None
    buy_threshold = _number(
        ((config.get("scoring") or {}).get("thresholds") or {}).get("buy")
    )
    if buy_threshold is not None:
        return buy_threshold
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
    baseline_metrics = cohort_metrics(rows)
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
            "rejected": len(rows) - len(selected),
            "metrics": cohort_metrics(selected),
            "impact": {
                "trades_approved": len(selected),
                "trades_rejected": len(rows) - len(selected),
                "tp_preserved": sum(row.get("outcome") == "TP_HIT" for row in selected),
                "tp_lost": baseline_metrics["tp"]
                - sum(row.get("outcome") == "TP_HIT" for row in selected),
                "sl_preserved": sum(row.get("outcome") == "SL_HIT" for row in selected),
                "sl_avoided": baseline_metrics["sl"]
                - sum(row.get("outcome") == "SL_HIT" for row in selected),
                "timeout_preserved": sum(
                    row.get("outcome") == "TIMEOUT" for row in selected
                ),
                "timeout_avoided": baseline_metrics["timeout"]
                - sum(row.get("outcome") == "TIMEOUT" for row in selected),
                "volume_reduction": (
                    (len(rows) - len(selected)) / len(rows) if rows else 0.0
                ),
                "score_minimum": threshold,
            },
        })
    return simulations


def select_simulated_points(
    simulations: Sequence[Mapping[str, Any]],
    policy: Mapping[str, Any],
) -> int | None:
    """Choose a penalty from measured alternatives, never from a default."""
    baseline = next(
        (
            item
            for item in simulations
            if int(item.get("points") or 0) == 0 and item.get("status") == "SIMULATED"
        ),
        None,
    )
    if baseline is None:
        return None
    baseline_metrics = baseline.get("metrics") or {}
    baseline_tp = int(baseline_metrics.get("tp") or 0)
    baseline_sl = int(baseline_metrics.get("sl") or 0)
    baseline_selected = int(baseline.get("selected") or 0)
    eligible: list[tuple[tuple[float, float, float, int], int]] = []
    for item in simulations:
        points = int(item.get("points") or 0)
        if points >= 0 or item.get("status") != "SIMULATED":
            continue
        metrics = item.get("metrics") or {}
        selected = int(item.get("selected") or 0)
        tp = int(metrics.get("tp") or 0)
        sl = int(metrics.get("sl") or 0)
        retention = selected / baseline_selected if baseline_selected else 0.0
        tp_loss_rate = (baseline_tp - tp) / baseline_tp if baseline_tp else 0.0
        sl_reduction_rate = (baseline_sl - sl) / baseline_sl if baseline_sl else 0.0
        if (
            retention < float(policy.get("score_global_replay_min_retention", 0.70))
            or tp_loss_rate
            > float(policy.get("score_global_replay_max_tp_loss_rate", 0.05))
            or sl_reduction_rate
            < float(policy.get("score_global_replay_min_sl_reduction_rate", 0.02))
        ):
            continue
        eligible.append(
            (
                (
                    sl_reduction_rate,
                    -tp_loss_rate,
                    float(metrics.get("avg_pnl_pct") or float("-inf")),
                    points,
                ),
                points,
            )
        )
    return max(eligible, default=((), None))[1]


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
        own = [
            row
            for row in approved_by_profile.get(profile_id, [])
            if str(row.get("profile_version_id") or "")
            == str(champion.get("profile_version_id") or "")
            and str(row.get("score_engine_version_id") or "")
            == str(champion.get("score_engine_version_id") or "")
            and str(row.get("profile_config_hash") or "")
            == str(champion.get("config_hash") or "")
            and str(row.get("score_engine_config_hash") or "")
            == str(champion.get("score_engine_config_hash") or "")
        ]
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
            selected_points = select_simulated_points(simulations, policy)
            if selected_points is None:
                continue
            rule = {
                "id": candidate_id,
                "rule_id": candidate_id,
                "indicator": bucket["indicator"],
                "bucket": bucket["bucket_label"],
                **_rule_condition(bucket),
                "points": selected_points,
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
                "profile_version_id": str(champion.get("profile_version_id") or ""),
                "score_engine_version_id": str(
                    champion.get("score_engine_version_id") or ""
                ),
                "profile_config_hash": champion.get("config_hash"),
                "score_engine_config_hash": champion.get("score_engine_config_hash"),
                "timeframe": (champion.get("config") or {}).get("default_timeframe"),
                "action_type": "ADD_SCORE_PENALTY",
                "target_path": "/scoring/generated_rules",
                "current_value": None,
                "proposed_value": rule,
                "discovery": {
                    "period": cohort_period(discovery),
                    **discovery_effect,
                },
                "validation": {
                    "status": status,
                    "period": cohort_period(validation),
                    **validation_effect,
                },
                "simulations": simulations,
                "selected_simulation_points": selected_points,
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

    def validate_metrics(path: str, metrics: Mapping[str, Any]) -> None:
        closed = int(metrics.get("closed") or 0)
        components = sum(
            int(metrics.get(key) or 0)
            for key in ("tp", "sl", "timeout", "other_outcomes")
        )
        if closed != components:
            errors.append(f"INCONSISTENT_COUNTS:{path}")
        for count_key, rate_key in (
            ("tp", "tp_rate"),
            ("sl", "sl_rate"),
            ("timeout", "timeout_rate"),
        ):
            rate = metrics.get(rate_key)
            expected = int(metrics.get(count_key) or 0) / closed if closed else None
            if rate is not None and not 0.0 <= float(rate) <= 1.0:
                errors.append(f"RATE_OUT_OF_RANGE:{path}:{rate_key}")
            if (rate is None) != (expected is None) or (
                rate is not None and not math.isclose(float(rate), expected, abs_tol=1e-12)
            ):
                errors.append(f"INCONSISTENT_RATE:{path}:{rate_key}")
        pnl_n = int(metrics.get("pnl_n") or 0)
        avg_pnl = metrics.get("avg_pnl_pct")
        pnl_sum = metrics.get("pnl_sum_pct")
        if pnl_n and avg_pnl is not None and pnl_sum is not None and not math.isclose(
            float(avg_pnl) * pnl_n,
            float(pnl_sum),
            rel_tol=1e-9,
            abs_tol=1e-9,
        ):
            errors.append(f"INCONSISTENT_PNL:{path}")

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
        validate_metrics(f"source_metrics.{source}", metrics or {})
    for cohort_name, cohort in (payload.get("cohorts") or {}).items():
        definition = str((cohort or {}).get("definition") or "").lower().replace(" ", "")
        metrics = (cohort or {}).get("metrics") or {}
        validate_metrics(f"cohorts.{cohort_name}.metrics", metrics)
        closed = int(metrics.get("closed") or 0)
        if (
            "outcome=tp_hit" in definition
            and closed > 0
            and int(metrics.get("tp") or 0) == closed
        ) or (
            "outcome=sl_hit" in definition
            and closed > 0
            and int(metrics.get("sl") or 0) == closed
        ):
            errors.append(f"TAUTOLOGICAL_OUTCOME_COHORT:{cohort_name}")
    matrix = payload.get("confusion_matrix") or {}
    if any(int(matrix.get(key) or 0) < 0 for key in ("tp", "fp", "fn", "tn")):
        errors.append("INVALID_CONFUSION_MATRIX")
    if not payload.get("candidates"):
        warnings.append("NO_VALIDATED_PROFILE_CANDIDATES")
    expected_simulation_points = set(PENALTY_ALTERNATIVES + BONUS_ALTERNATIVES)
    candidate_ids: set[str] = set()
    candidates_by_profile: Counter[str] = Counter()
    for candidate in payload.get("candidates") or []:
        candidate_id = str(candidate.get("candidate_id") or "")
        if not candidate_id or candidate_id in candidate_ids:
            errors.append("DUPLICATE_OR_EMPTY_CANDIDATE_ID")
        candidate_ids.add(candidate_id)
        profile_id = str(candidate.get("profile_id") or "")
        candidates_by_profile[profile_id] += 1
        for required in (
            "profile_id",
            "profile_version_id",
            "score_engine_version_id",
            "profile_config_hash",
            "score_engine_config_hash",
        ):
            if not candidate.get(required):
                errors.append(f"BLOCKED_SCOPE_MISMATCH:{candidate_id}:{required}")
        simulations = candidate.get("simulations") or []
        simulated_points = {
            int(item.get("points") or 0)
            for item in simulations
            if item.get("status") == "SIMULATED"
        }
        if simulated_points != expected_simulation_points:
            errors.append(f"BLOCKED_IMPACT_NOT_SIMULATED:{candidate_id}")
        selected_points = candidate.get("selected_simulation_points")
        if (
            selected_points is None
            or int(selected_points) == 0
            or int(selected_points) not in simulated_points
            or int((candidate.get("proposed_value") or {}).get("points") or 0)
            != int(selected_points)
        ):
            errors.append(f"INVALID_SIMULATED_SELECTION:{candidate_id}")
    expected_overlap_pairs = sum(
        count * (count - 1) // 2 for count in candidates_by_profile.values()
    )
    overlap_pairs = {
        (
            str(item.get("profile_id") or ""),
            *sorted(
                (
                    str(item.get("candidate_a") or ""),
                    str(item.get("candidate_b") or ""),
                )
            ),
        )
        for item in payload.get("overlap_analysis") or []
    }
    if len(overlap_pairs) != expected_overlap_pairs:
        errors.append("BLOCKED_RULE_OVERLAP_NOT_SIMULATED")
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
    if response.get("report_schema_version") != AI_REPORT_SCHEMA_VERSION:
        raise ValueError("ai_report_schema_mismatch")

    allowed_by_profile: dict[str, set[str]] = defaultdict(set)
    known_by_profile: dict[str, set[str]] = defaultdict(set)
    for candidate in payload.get("candidates") or []:
        profile_id = str(candidate["profile_id"])
        candidate_id = str(candidate["candidate_id"])
        known_by_profile[profile_id].add(candidate_id)
        if (candidate.get("validation") or {}).get("status") == "VALIDATED":
            allowed_by_profile[profile_id].add(candidate_id)

    def clean_text(value: Any) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("AI_RESPONSE_REJECTED_EMPTY_REPORT_TEXT")
        if any(
            ord(char) < 32 and char not in ("\n", "\r")
            for char in text
        ):
            raise ValueError("AI_RESPONSE_REJECTED_CORRUPTED_TEXT")
        if "\ufffd" in text or re.search(
            r"(?i)(?:\\t|\\r|\\n)|\b(?:terminado|erminado|texto|exto)\b",
            text,
        ):
            raise ValueError("AI_RESPONSE_REJECTED_CORRUPTED_TEXT")
        return text

    def clean_texts(value: Any, limit: int = 12) -> list[str]:
        return [clean_text(item) for item in list(value or [])[:limit]]

    executive_summary = clean_texts(response.get("executive_summary"), 8)
    if len(executive_summary) < 4:
        raise ValueError("AI_RESPONSE_REJECTED_INCOMPLETE_REPORT")

    data_quality = response.get("data_quality") or {}
    cohort_analysis = response.get("cohort_analysis") or {}
    confusion_analysis = response.get("confusion_matrix_analysis") or {}
    selected: set[str] = set()
    bounded: list[dict[str, Any]] = []
    seen_profiles: set[str] = set()
    for item in list(response.get("profile_recommendations") or [])[:60]:
        profile_id = str(item.get("profile_id") or "")
        if profile_id in seen_profiles:
            raise ValueError("ai_duplicate_profile_recommendation")
        if profile_id not in known_by_profile:
            raise ValueError("ai_selected_unknown_profile")
        seen_profiles.add(profile_id)
        candidate_ids = list(dict.fromkeys(
            str(value) for value in item.get("selected_candidate_ids") or []
        ))[:3]
        if any(
            candidate_id not in allowed_by_profile.get(profile_id, set())
            for candidate_id in candidate_ids
        ):
            raise ValueError("ai_selected_unknown_or_cross_profile_candidate")
        selected.update(candidate_ids)
        bounded.append({
            "profile_id": profile_id,
            "technical_reading": clean_texts(item.get("technical_reading"), 8),
            "limitations": clean_texts(item.get("limitations"), 8),
            "recommendation": clean_text(item.get("recommendation")),
            "confidence": str(item.get("confidence") or "").strip().upper(),
            "priority": str(item.get("priority") or "").strip().upper(),
            "selected_candidate_ids": candidate_ids,
        })
        if bounded[-1]["confidence"] not in {"ALTA", "MEDIA", "BAIXA"}:
            raise ValueError("ai_invalid_confidence")
        if bounded[-1]["priority"] not in {"ALTA", "MEDIA", "BAIXA"}:
            raise ValueError("ai_invalid_priority")
    if seen_profiles != set(known_by_profile):
        raise ValueError("AI_RESPONSE_REJECTED_INCOMPLETE_PROFILE_COVERAGE")

    redundancies: list[dict[str, Any]] = []
    for item in list(response.get("redundancy_analysis") or [])[:50]:
        profile_id = str(item.get("profile_id") or "")
        candidate_ids = list(dict.fromkeys(
            str(value) for value in item.get("candidate_ids") or []
        ))[:6]
        if (
            profile_id not in known_by_profile
            or any(
                candidate_id not in known_by_profile[profile_id]
                for candidate_id in candidate_ids
            )
        ):
            raise ValueError("ai_redundancy_unknown_or_cross_profile_candidate")
        redundancies.append({
            "profile_id": profile_id,
            "candidate_ids": candidate_ids,
            "diagnosis": clean_text(item.get("diagnosis")),
            "recommendation": clean_text(item.get("recommendation")),
        })

    prioritization = response.get("prioritization") or {}
    priority_ids: dict[str, list[str]] = {
        "high": [],
        "medium": [],
        "low": [],
    }
    for item in bounded:
        level = {
            "ALTA": "high",
            "MEDIA": "medium",
            "BAIXA": "low",
        }[item["priority"]]
        priority_ids[level].extend(item["selected_candidate_ids"])
    priority_ids = {
        level: list(dict.fromkeys(candidate_ids))
        for level, candidate_ids in priority_ids.items()
    }

    clean = {
        "analysis_contract_version": ANALYSIS_CONTRACT_VERSION,
        "analysis_skill_version": ANALYSIS_SKILL_VERSION,
        "report_schema_version": AI_REPORT_SCHEMA_VERSION,
        "executive_summary": executive_summary,
        "data_quality": {
            "integrity_assessment": clean_texts(
                data_quality.get("integrity_assessment"), 12
            ),
            "limitations": clean_texts(data_quality.get("limitations"), 12),
        },
        "cohort_analysis": {
            "l3": clean_texts(cohort_analysis.get("l3"), 12),
            "l3_lab": clean_texts(cohort_analysis.get("l3_lab"), 12),
            "approved_combined": clean_texts(
                cohort_analysis.get("approved_combined"), 12
            ),
            "l3_rejected": clean_texts(
                cohort_analysis.get("l3_rejected"), 12
            ),
        },
        "confusion_matrix_analysis": {
            "interpretation": clean_texts(
                confusion_analysis.get("interpretation"), 12
            ),
            "operational_impact": clean_texts(
                confusion_analysis.get("operational_impact"), 12
            ),
        },
        "profile_recommendations": bounded,
        "redundancy_analysis": redundancies,
        "prioritization": {
            **priority_ids,
            "rationale": clean_texts(prioritization.get("rationale"), 12),
        },
        "next_steps": clean_texts(response.get("next_steps"), 12),
        "selected_candidate_ids": sorted(selected),
        "read_only_statement": (
            "Esta análise foi realizada em modo somente leitura. Nenhuma "
            "alteração foi aplicada ao incumbent, aos profiles ativos, ao "
            "dataset, ao treinamento, ao Auto-Pilot ou ao ambiente de produção."
        ),
        "governance_statement": (
            "As conclusões deste relatório são associativas e não causais. A "
            "coorte L3_REJECTED e as métricas globais foram utilizadas somente "
            "como contexto quando não havia atribuição profile-local. Nenhuma "
            "recomendação deve ser aplicada diretamente ao incumbent. Toda "
            "alteração deve passar por challenger versionado, replay "
            "point-in-time, validação shadow e aprovação humana."
        ),
        "verified_evidence": {
            "cutoff_at": payload.get("cutoff_at"),
            "lookback_days": payload.get("lookback_days"),
            "row_count": payload.get("row_count"),
            "deduplicated_row_count": payload.get("deduplicated_row_count"),
            "deduplication": payload.get("deduplication"),
            "cohorts": payload.get("cohorts"),
            "source_metrics": payload.get("source_metrics"),
            "confusion_matrix": payload.get("confusion_matrix"),
            "candidate_accounting": payload.get("candidate_accounting"),
            "candidates": payload.get("candidates"),
            "overlap_analysis": payload.get("overlap_analysis"),
            "safety": payload.get("safety"),
        },
    }

    numeric_values: list[float] = []

    def collect_numbers(value: Any) -> None:
        if isinstance(value, bool):
            return
        if isinstance(value, (int, float)) and math.isfinite(float(value)):
            numeric_values.append(float(value))
            return
        if isinstance(value, Mapping):
            for nested in value.values():
                collect_numbers(nested)
        elif isinstance(value, (list, tuple)):
            for nested in value:
                collect_numbers(nested)

    collect_numbers(payload)
    numeric_values.extend(
        (
            float(len(clean["selected_candidate_ids"])),
            float(len(clean["profile_recommendations"])),
        )
    )
    narratives = [
        *clean["executive_summary"],
        *clean["data_quality"]["integrity_assessment"],
        *clean["data_quality"]["limitations"],
        *clean["cohort_analysis"]["l3"],
        *clean["cohort_analysis"]["l3_lab"],
        *clean["cohort_analysis"]["approved_combined"],
        *clean["cohort_analysis"]["l3_rejected"],
        *clean["confusion_matrix_analysis"]["interpretation"],
        *clean["confusion_matrix_analysis"]["operational_impact"],
        *(
            text
            for item in clean["profile_recommendations"]
            for text in (
                *item["technical_reading"],
                *item["limitations"],
                item["recommendation"],
            )
        ),
        *(
            text
            for item in clean["redundancy_analysis"]
            for text in (item["diagnosis"], item["recommendation"])
        ),
        *clean["prioritization"]["rationale"],
        *clean["next_steps"],
    ]
    for narrative in narratives:
        for token in re.findall(
            r"(?<![\w-])[-+]?\d+(?:[.,]\d+)?%?",
            str(narrative),
        ):
            is_percent = token.endswith("%")
            raw = token[:-1] if is_percent else token
            if "," in raw and "." not in raw and len(raw.rsplit(",", 1)[-1]) == 3:
                raw = raw.replace(",", "")
            else:
                raw = raw.replace(",", ".")
            try:
                cited_raw = float(raw)
            except ValueError:
                continue
            decimals = len(raw.rsplit(".", 1)[-1]) if "." in raw else 0
            display_tolerance = 0.5 * (10 ** (-decimals)) + 1e-12
            cited_candidates = [cited_raw]
            if is_percent or abs(cited_raw) > 1:
                cited_candidates.append(cited_raw / 100.0)
            if not any(
                math.isclose(
                    cited,
                    known,
                    rel_tol=0.0,
                    abs_tol=(
                        display_tolerance / 100.0
                        if cited == cited_raw / 100.0 and cited != cited_raw
                        else display_tolerance
                    ),
                )
                for cited in cited_candidates
                for known in numeric_values
            ):
                raise ValueError(
                    "AI_RESPONSE_REJECTED_NUMERIC_OR_SCOPE_MISMATCH"
                )
    return clean


def build_bounded_ai_context(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Return the complete decision surface without verbose persisted detail.

    The run keeps the full deterministic payload.  The external model receives
    the same metrics at a bounded shape: candidates are present exactly once,
    policy/provider capability blobs are omitted, and simulation metrics retain
    only the fields that can affect the executive recommendation.
    """

    def compact_metrics(value: Mapping[str, Any] | None) -> dict[str, Any] | None:
        if not value:
            return None
        return {
            key: value.get(key)
            for key in (
                "closed",
                "tp",
                "sl",
                "timeout",
                "tp_rate",
                "sl_rate",
                "timeout_rate",
                "avg_pnl_pct",
                "pnl_sum_pct",
                "distinct_symbols",
                "distinct_days",
                "max_single_symbol_share",
                "max_single_day_share",
            )
            if key in value
        }

    candidates = []
    for item in payload.get("candidates") or []:
        discovery = item.get("discovery") or {}
        validation = item.get("validation") or {}
        candidates.append({
            "candidate_id": item.get("candidate_id"),
            "candidate_definition_id": item.get("candidate_definition_id"),
            "scope": item.get("scope"),
            "profile_id": item.get("profile_id"),
            "profile_name": item.get("profile_name"),
            "action_type": item.get("action_type"),
            "target_path": item.get("target_path"),
            "proposed_value": item.get("proposed_value"),
            "sources": item.get("sources"),
            "discovery": {
                "present": compact_metrics(discovery.get("present")),
                "absent": compact_metrics(discovery.get("absent")),
                "sl_rate_delta_present_minus_absent": discovery.get(
                    "sl_rate_delta_present_minus_absent"
                ),
            },
            "validation": {
                "status": validation.get("status"),
                "present": compact_metrics(validation.get("present")),
                "absent": compact_metrics(validation.get("absent")),
                "sl_rate_delta_present_minus_absent": validation.get(
                    "sl_rate_delta_present_minus_absent"
                ),
            },
            "simulations": [{
                "points": simulation.get("points"),
                "status": simulation.get("status"),
                "selected": simulation.get("selected"),
                "metrics": compact_metrics(simulation.get("metrics")),
            } for simulation in item.get("simulations") or []],
        })

    counterfactual = payload.get("counterfactual_analysis") or {}
    counterfactual_buckets = sorted(
        list(counterfactual.get("buckets") or []),
        key=lambda item: int(((item.get("present") or {}).get("closed") or 0)),
        reverse=True,
    )[:50]
    compact_counterfactual = {
        "scope": counterfactual.get("scope"),
        "source": counterfactual.get("source"),
        "profile_attribution_allowed": counterfactual.get(
            "profile_attribution_allowed"
        ),
        "baseline": compact_metrics(counterfactual.get("baseline")),
        "buckets": [{
            "bucket": item.get("bucket"),
            "indicator": item.get("indicator"),
            "present": compact_metrics(item.get("present")),
            "absent": compact_metrics(item.get("absent")),
            "sl_rate_delta_present_minus_absent": item.get(
                "sl_rate_delta_present_minus_absent"
            ),
        } for item in counterfactual_buckets],
    }
    context = {
        "analysis_contract_version": payload.get("analysis_contract_version"),
        "analysis_skill_version": payload.get("analysis_skill_version"),
        "cutoff_at": payload.get("cutoff_at"),
        "lookback_days": payload.get("lookback_days"),
        "row_count": payload.get("row_count"),
        "deduplicated_row_count": payload.get("deduplicated_row_count"),
        "deduplication": payload.get("deduplication"),
        "cohorts": payload.get("cohorts"),
        "source_metrics": payload.get("source_metrics"),
        "confusion_matrix": payload.get("confusion_matrix"),
        "candidate_accounting": payload.get("candidate_accounting"),
        "profile_count": len(
            {
                str(item.get("profile_id"))
                for item in payload.get("candidates") or []
                if item.get("profile_id")
            }
        ),
        "counterfactual_analysis": compact_counterfactual,
        "overlap_analysis": payload.get("overlap_analysis"),
        "candidates": candidates,
        "pre_ai_validation": payload.get("pre_ai_validation"),
        "safety": payload.get("safety"),
        "full_payload_persisted": True,
    }
    serialized = json.dumps(_json(context), separators=(",", ":"), default=str)
    context["bounded_context"] = {
        "char_count": len(serialized),
        "sha256": hashlib.sha256(serialized.encode()).hexdigest(),
    }
    return context
