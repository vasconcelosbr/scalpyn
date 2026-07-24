"""Global Profile Score Intelligence analysis and shadow-only optimization.

The service deliberately separates analytical evidence from ML datasets.  It
reads official point-in-time rows, creates immutable proposal envelopes, and
can create SHADOW profile versions only after a deterministic replay gate.
It never mutates an incumbent, training row, model registry, or promotion
state.
"""

from __future__ import annotations

import asyncio
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
from statistics import mean
from typing import Any, Mapping, Sequence
from uuid import UUID

from sqlalchemy import delete, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.ai_skill import AiSkill
from ..models.config_profile import ConfigProfile
from ..models.profile_score_optimization import (
    ProfileIntelligenceAIModelAudit,
    ProfileScoreOptimizationChallenger,
    ProfileScoreOptimizationRun,
    ProfileScorePerformanceDaily,
    ProfileScoreReplayResult,
)
from .ai_keys_service import get_decrypted_api_key
from .calibration_orchestrator_v2 import content_hash
from .indicator_lift_service import _get_indicator_buckets
from .profile_intelligence_contract import official_params, official_where
from .profile_intelligence_manual_service import apply_manual_action
from .profile_intelligence_ai_models import (
    DEFAULT_AI_MODEL,
    SUPPORTED_AI_MODELS,
    configured_model,
    list_models_for_key,
    retrieve_model_for_key,
)
from .profile_intelligence_analysis_v2 import (
    AI_REPORT_SCHEMA_V2,
    ANALYSIS_CONTRACT_VERSION,
    ANALYSIS_SKILL_VERSION,
    PROFILE_INTELLIGENCE_ANALYSIS_SKILL_V2,
    build_candidates,
    build_bounded_ai_context,
    build_overlap_analysis,
    cohort_metrics,
    confusion_matrix,
    deduplicate_rows,
    validate_ai_response_against_payload,
    validate_analysis_payload,
)
from .profile_versioning_v2 import create_shadow_profile_version


DATASET_CONTRACT = ANALYSIS_CONTRACT_VERSION
ANALYSIS_SOURCES = ("L1_SPECTRUM", "L3", "L3_LAB", "L3_REJECTED")
CLOSED_OUTCOMES = ("TP_HIT", "SL_HIT", "TIMEOUT")
CHAMPION_SOURCE = "PI_CHAMPION_CONTROL"
CHALLENGER_SOURCE = "PI_CHALLENGER"

DEFAULT_POLICY: dict[str, Any] = {
    "ai_provider": "anthropic",
    "ai_model": "claude-haiku-4-5-20251001",
    "ai_model_status": "NOT_TESTED",
    "analysis_skill_version": ANALYSIS_SKILL_VERSION,
    "score_global_rapid_sl_candles": 12,
    "score_global_max_analysis_rows": 100000,
    "score_global_min_bucket_trades": 30,
    "score_global_penalty_points": -5,
    "score_global_max_changes_per_profile": 3,
    "score_global_ai_timeout_seconds": 180,
    "score_global_replay_min_retention": 0.70,
    "score_global_replay_max_tp_loss_rate": 0.05,
    "score_global_replay_min_sl_reduction_rate": 0.02,
    "score_global_challenger_min_days": 7,
    "score_global_challenger_min_closed": 100,
    "score_global_challenger_min_tp": 20,
    "score_global_challenger_min_sl": 20,
    "score_global_challenger_min_distinct_symbols": 3,
    "score_global_challenger_min_distinct_days": 3,
    "score_global_challenger_max_single_symbol_share": 0.40,
    "score_global_challenger_max_single_day_share": 0.40,
}

AI_REPORT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "executive_summary": {
            "type": "string",
            "description": "Resumo executivo conciso, obrigatório e não vazio.",
        },
        "global_diagnosis": {
            "type": "array",
            "items": {"type": "string"},
            "description": "No máximo 12 diagnósticos globais concisos.",
        },
        "profile_recommendations": {
            "type": "array",
            "description": "No máximo 60 recomendações, somente quando houver evidência.",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "profile_id": {"type": "string"},
                    "diagnosis": {
                        "type": "string",
                    },
                    "selected_candidate_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "No máximo 3 IDs únicos fornecidos para este profile.",
                    },
                },
                "required": [
                    "profile_id",
                    "diagnosis",
                    "selected_candidate_ids",
                ],
            },
        },
        "risks": {
            "type": "array",
            "items": {"type": "string"},
            "description": "No máximo 12 riscos concisos.",
        },
        "safeguards": {
            "type": "array",
            "items": {"type": "string"},
            "description": "No máximo 12 salvaguardas concisas.",
        },
    },
    "required": [
        "executive_summary",
        "global_diagnosis",
        "profile_recommendations",
        "risks",
        "safeguards",
    ],
}

# Keep the public constant import-compatible while enforcing the v2 response
# contract for all new runs.
AI_REPORT_SCHEMA = AI_REPORT_SCHEMA_V2


def _json(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json(item) for item in value]
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "as_tuple"):
        return float(value)
    return value


def _hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(_json(value), sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def _strip_json(raw: str) -> str:
    value = raw.strip()
    if value.startswith("```"):
        lines = value.splitlines()
        value = "\n".join(lines[1:-1])
    return value.strip()


def _parse_ai_report_response(response: Any) -> dict[str, Any]:
    stop_reason = getattr(response, "stop_reason", None)
    if stop_reason == "max_tokens":
        raise ValueError("profile_score_ai_output_truncated")
    if stop_reason == "refusal":
        raise ValueError("profile_score_ai_refused")
    raw = "".join(
        str(getattr(block, "text", "") or "")
        for block in (getattr(response, "content", None) or [])
    )
    try:
        parsed = json.loads(_strip_json(raw))
    except (json.JSONDecodeError, TypeError) as exc:
        raise ValueError("invalid_profile_score_ai_response") from exc
    if not isinstance(parsed, dict):
        raise ValueError("invalid_profile_score_ai_response")
    return parsed


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _features(row: Mapping[str, Any]) -> dict[str, Any]:
    value = row.get("features_snapshot")
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            value = {}
    return dict(value or {}) if isinstance(value, Mapping) else {}


def _metrics(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    counts = Counter(str(row.get("outcome")) for row in rows)
    total = len(rows)
    pnls = [_number(row.get("pnl_pct")) for row in rows]
    pnls = [value for value in pnls if value is not None]
    return {
        "closed": total,
        "tp": counts["TP_HIT"],
        "sl": counts["SL_HIT"],
        "timeout": counts["TIMEOUT"],
        "tp_rate": counts["TP_HIT"] / total if total else None,
        "sl_rate": counts["SL_HIT"] / total if total else None,
        "avg_pnl_pct": mean(pnls) if pnls else None,
        "pnl_sum_pct": sum(pnls) if pnls else None,
    }


def _diversity(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    symbols = Counter(str(row.get("symbol")) for row in rows if row.get("symbol"))
    days = Counter(str(row.get("created_at"))[:10] for row in rows if row.get("created_at"))
    total = len(rows)
    return {
        "distinct_symbols": len(symbols),
        "distinct_days": len(days),
        "max_single_symbol_share": max(symbols.values(), default=0) / total if total else 0.0,
        "max_single_day_share": max(days.values(), default=0) / total if total else 0.0,
    }


def _rapid_sl(row: Mapping[str, Any], candles: int) -> bool:
    holding = _number(row.get("holding_seconds"))
    return str(row.get("outcome")) == "SL_HIT" and holding is not None and holding <= candles * 300


def _condition_from_bucket(bucket: Mapping[str, Any]) -> dict[str, Any]:
    if bucket.get("range_min") is not None and bucket.get("range_max") is not None:
        return {
            "operator": "between",
            "min": bucket["range_min"],
            "max": bucket["range_max"],
        }
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


def _matches(feature_map: Mapping[str, Any], bucket: Mapping[str, Any]) -> bool:
    value = _number(feature_map.get(str(bucket["indicator"])))
    if value is None:
        return False
    try:
        return bool(bucket["condition"](value))
    except Exception:
        return False


def _evaluate_allow(config: Mapping[str, Any], row: Mapping[str, Any]) -> bool:
    """Replay the current production profile contract on one immutable row."""
    from ..tasks.pipeline_scan import _RobustScoreShim
    from .profile_engine import ProfileEngine

    feature_map = _features(row)
    asset = {
        **feature_map,
        "indicators": feature_map,
        "symbol": row.get("symbol"),
        "_score": feature_map.get("score", feature_map.get("alpha_score")),
        "alpha_score": feature_map.get("alpha_score", feature_map.get("score")),
        "score": feature_map.get("score", feature_map.get("alpha_score")),
    }
    engine = ProfileEngine(dict(config))
    engine.score_engine = _RobustScoreShim(
        thresholds=(config.get("scoring") or {}).get("thresholds"),
        manual_rules=[
            rule for rule in ((config.get("scoring") or {}).get("generated_rules") or [])
            if isinstance(rule, dict) and rule.get("manual_profile_intelligence") is True
        ],
    )
    processed = engine.evaluate_asset(asset)
    if processed.get("blocked") or not processed.get("passed_filter", False):
        return False
    signal_conditions = (
        (config.get("entry_triggers") or {}).get("conditions")
        or (config.get("signals") or {}).get("conditions")
        or []
    )
    return bool((processed.get("signal") or {}).get("triggered")) if signal_conditions else True


def _candidate_stats(
    prepared_rows: Sequence[tuple[Mapping[str, Any], Mapping[str, Any]]],
    rapid_candles: int,
    bucket: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    stats = {
        "cases": 0,
        "tp": 0,
        "sl": 0,
        "timeout": 0,
        "rapid_sl": 0,
        "pnl_sum": 0.0,
        "pnl_count": 0,
        "sources": set(),
    }
    for row, feature_map in prepared_rows:
        if bucket is not None and not _matches(feature_map, bucket):
            continue
        stats["cases"] += 1
        outcome = str(row.get("outcome"))
        if outcome == "TP_HIT":
            stats["tp"] += 1
        elif outcome == "SL_HIT":
            stats["sl"] += 1
        elif outcome == "TIMEOUT":
            stats["timeout"] += 1
        if _rapid_sl(row, rapid_candles):
            stats["rapid_sl"] += 1
        pnl = _number(row.get("pnl_pct"))
        if pnl is not None:
            stats["pnl_sum"] += pnl
            stats["pnl_count"] += 1
        stats["sources"].add(str(row.get("source")))
    return stats


def _merge_candidate_stats(*items: Mapping[str, Any]) -> dict[str, Any]:
    merged = {
        "cases": 0,
        "tp": 0,
        "sl": 0,
        "timeout": 0,
        "rapid_sl": 0,
        "pnl_sum": 0.0,
        "pnl_count": 0,
        "sources": set(),
    }
    for item in items:
        for key in ("cases", "tp", "sl", "timeout", "rapid_sl", "pnl_count"):
            merged[key] += int(item.get(key) or 0)
        merged["pnl_sum"] += float(item.get("pnl_sum") or 0.0)
        merged["sources"].update(item.get("sources") or ())
    return merged


class ProfileScoreOptimizationService:
    async def policy(self, db: AsyncSession, user_id: UUID) -> dict[str, Any]:
        result = await db.execute(text("""
            SELECT config_json FROM config_profiles
             WHERE user_id=:uid AND config_type='profile_intelligence'
               AND is_active IS TRUE AND pool_id IS NULL
             ORDER BY updated_at DESC LIMIT 1
        """), {"uid": str(user_id)})
        row = result.mappings().first()
        raw = row["config_json"] if row else {}
        if isinstance(raw, str):
            raw = json.loads(raw)
        return {**DEFAULT_POLICY, **dict(raw or {})}

    async def ai_models(self, db: AsyncSession, user_id: UUID) -> dict[str, Any]:
        policy = await self.policy(db, user_id)
        current = configured_model(policy)
        return {
            "provider": "anthropic",
            "current_model": current,
            "default_model": DEFAULT_AI_MODEL,
            "model_status": policy.get("ai_model_status") or "NOT_TESTED",
            "verified_at": policy.get("ai_model_verified_at"),
            "analysis_skill_version": ANALYSIS_SKILL_VERSION,
            "models": [
                {
                    "id": model_id,
                    **metadata,
                    "status": (
                        policy.get("ai_model_status")
                        if model_id == current and policy.get("ai_model_status")
                        else "NOT_TESTED"
                    ),
                    "available": (
                        policy.get("ai_model_status") == "AVAILABLE"
                        if model_id == current
                        else None
                    ),
                    "capabilities": (
                        policy.get("ai_model_capabilities") or {}
                        if model_id == current
                        else {}
                    ),
                }
                for model_id, metadata in SUPPORTED_AI_MODELS.items()
            ],
        }

    async def refresh_ai_models(
        self, db: AsyncSession, user_id: UUID
    ) -> dict[str, Any]:
        result = await list_models_for_key(db, user_id)
        result["current_model"] = configured_model(await self.policy(db, user_id))
        return result

    async def test_ai_model(
        self, db: AsyncSession, user_id: UUID, model_id: str
    ) -> dict[str, Any]:
        return await retrieve_model_for_key(db, user_id, model_id)

    async def save_ai_model(
        self,
        db: AsyncSession,
        user_id: UUID,
        model_id: str,
        reason: str | None = None,
    ) -> dict[str, Any]:
        verification = await retrieve_model_for_key(db, user_id, model_id)
        if not verification.get("available"):
            raise ValueError(
                f"ai_model_not_available:{verification.get('status') or 'UNAVAILABLE'}"
            )
        config = await db.scalar(
            select(ConfigProfile)
            .where(
                ConfigProfile.user_id == user_id,
                ConfigProfile.config_type == "profile_intelligence",
                ConfigProfile.pool_id.is_(None),
                ConfigProfile.is_active.is_(True),
            )
            .order_by(ConfigProfile.updated_at.desc())
            .limit(1)
        )
        previous = configured_model(config.config_json if config else {})
        updated = {
            **DEFAULT_POLICY,
            **dict((config.config_json if config else {}) or {}),
            "ai_provider": "anthropic",
            "ai_model": model_id,
            "ai_model_verified_at": verification["verified_at"],
            "ai_model_status": verification["status"],
            "ai_model_capabilities": verification.get("capabilities") or {},
            "analysis_skill_version": ANALYSIS_SKILL_VERSION,
        }
        if config is None:
            config = ConfigProfile(
                user_id=user_id,
                pool_id=None,
                config_type="profile_intelligence",
                config_json=updated,
                is_active=True,
            )
            db.add(config)
            await db.flush()
        else:
            config.config_json = updated
        db.add(ProfileIntelligenceAIModelAudit(
            user_id=user_id,
            config_id=config.id,
            previous_model=previous,
            new_model=model_id,
            status=verification["status"],
            request_id=verification.get("request_id"),
            reason=(reason or "Profile Intelligence Settings")[:2000],
            capabilities=verification.get("capabilities") or {},
        ))
        await db.flush()
        return {
            **verification,
            "previous_model": previous,
            "current_model": model_id,
            "analysis_skill_version": ANALYSIS_SKILL_VERSION,
        }

    async def _official_rows(
        self, db: AsyncSession, user_id: UUID, lookback_days: int, cutoff: datetime,
        limit: int,
    ) -> tuple[list[dict[str, Any]], bool]:
        params = {
            "uid": str(user_id),
            "window_start": cutoff - timedelta(days=lookback_days),
            "cutoff": cutoff,
            "limit": limit + 1,
            **official_params(),
        }
        result = await db.execute(text(f"""
            SELECT st.id,st.source,st.profile_id,st.profile_version_id,
                   st.score_engine_version_id,st.symbol,st.timeframe,st.outcome,
                   st.pnl_pct,st.holding_seconds,st.mae_pct,st.mfe_pct,
                   st.created_at,st.completed_at,st.features_snapshot,
                   st.decision_id,st.event_id,st.ranking_id,
                   st.profile_config_hash,st.score_engine_config_hash
              FROM shadow_trades st
             WHERE st.user_id=:uid
               AND st.source IN ('L1_SPECTRUM','L3','L3_LAB','L3_REJECTED')
               AND st.outcome IN ('TP_HIT','SL_HIT','TIMEOUT')
               AND st.created_at >= GREATEST(CAST(:window_start AS TIMESTAMPTZ),
                                             CAST(:native_capture_start_at AS TIMESTAMPTZ))
               AND st.created_at <= CAST(:cutoff AS TIMESTAMPTZ)
               AND {official_where('st')}
             ORDER BY st.created_at DESC
             LIMIT CAST(:limit AS INTEGER)
        """), params)
        raw = [dict(row) for row in result.mappings().all()]
        return raw[:limit], len(raw) > limit

    async def _champions(self, db: AsyncSession, user_id: UUID) -> list[dict[str, Any]]:
        result = await db.execute(text("""
            SELECT p.id AS profile_id,p.name AS profile_name,pv.id AS profile_version_id,
                   pv.version_number,pv.config,pv.config_hash,pv.score_engine_version_id,
                   sev.config_hash AS score_engine_config_hash
              FROM profiles p
              JOIN pipeline_watchlists pw ON pw.profile_id=p.id AND upper(pw.level)='L3'
              JOIN profile_versions pv ON pv.profile_id=p.id
                   AND pv.status='CHAMPION' AND pv.is_active IS TRUE
              JOIN score_engine_versions sev ON sev.id=pv.score_engine_version_id
             WHERE p.user_id=:uid AND p.is_active IS TRUE
               AND p.is_shadow_only IS FALSE AND p.generated_by IS NULL
             ORDER BY p.name
        """), {"uid": str(user_id)})
        return [dict(row) for row in result.mappings().all()]

    def _quadrants(self, rows: Sequence[Mapping[str, Any]], rapid_candles: int) -> dict[str, Any]:
        approved = [row for row in rows if row.get("source") in {"L3", "L3_LAB"}]
        rejected = [row for row in rows if row.get("source") == "L3_REJECTED"]
        groups = {
            "approved_tp": [row for row in approved if row.get("outcome") == "TP_HIT"],
            "approved_sl": [row for row in approved if row.get("outcome") == "SL_HIT"],
            "rejected_rapid_sl": [row for row in rejected if _rapid_sl(row, rapid_candles)],
            "rejected_tp": [row for row in rejected if row.get("outcome") == "TP_HIT"],
        }
        return {
            name: {**_metrics(items), **_diversity(items), "trade_ids": [str(row["id"]) for row in items[:100]]}
            for name, items in groups.items()
        }

    def _candidates(
        self,
        rows: Sequence[Mapping[str, Any]],
        champions: Sequence[Mapping[str, Any]],
        policy: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        min_cases = int(policy["score_global_min_bucket_trades"])
        penalty = float(policy["score_global_penalty_points"])
        max_changes = int(policy["score_global_max_changes_per_profile"])
        rapid_candles = int(policy["score_global_rapid_sl_candles"])
        buckets = list(_get_indicator_buckets())
        prepared_rejected = [
            (row, _features(row))
            for row in rows
            if row.get("source") == "L3_REJECTED"
        ]
        prepared_approved: dict[str, list[tuple[Mapping[str, Any], Mapping[str, Any]]]] = (
            defaultdict(list)
        )
        for row in rows:
            if row.get("source") in {"L3", "L3_LAB"}:
                prepared_approved[str(row.get("profile_id") or "")].append(
                    (row, _features(row))
                )

        # Rejected evidence is global and identical for every profile.  Compute
        # each bucket once instead of rescanning the same cohort for every
        # champion (the previous profile x bucket x row loop exceeded 5 min).
        rejected_baseline = _candidate_stats(prepared_rejected, rapid_candles)
        rejected_by_bucket = [
            _candidate_stats(prepared_rejected, rapid_candles, bucket)
            for bucket in buckets
        ]
        candidates: list[dict[str, Any]] = []
        for champion in champions:
            profile_id = str(champion["profile_id"])
            own = prepared_approved.get(profile_id, [])
            own_baseline = _candidate_stats(own, rapid_candles)
            baseline = _merge_candidate_stats(rejected_baseline, own_baseline)
            baseline_sl = (
                baseline["sl"] / baseline["cases"] if baseline["cases"] else 0.0
            )
            ranked: list[dict[str, Any]] = []
            for bucket, global_stats in zip(buckets, rejected_by_bucket):
                own_stats = _candidate_stats(own, rapid_candles, bucket)
                matched = _merge_candidate_stats(global_stats, own_stats)
                cases = int(matched["cases"])
                if cases < min_cases:
                    continue
                rapid = int(matched["rapid_sl"])
                loss_rate = (
                    (matched["sl"] + rapid) / max(cases + rapid, 1)
                )
                avg_pnl = (
                    matched["pnl_sum"] / matched["pnl_count"]
                    if matched["pnl_count"]
                    else None
                )
                if avg_pnl is None or avg_pnl >= 0:
                    continue
                if loss_rate <= baseline_sl:
                    continue
                condition = _condition_from_bucket(bucket)
                candidate_id = f"pi-global-{profile_id[:8]}-{bucket['bucket_label']}"
                rule = {
                    "id": candidate_id,
                    "rule_id": candidate_id,
                    "indicator": bucket["indicator"],
                    **condition,
                    "points": penalty,
                    "category": "signal",
                    "name": f"PI global penalty: {bucket['bucket_label']}",
                    "description": "Penalidade shadow baseada nos quatro quadrantes point-in-time.",
                    "manual_profile_intelligence": True,
                }
                ranked.append({
                    "candidate_id": candidate_id,
                    "profile_id": profile_id,
                    "profile_name": champion["profile_name"],
                    "action_type": "ADD_SCORE_PENALTY",
                    "target_path": "/scoring/generated_rules",
                    "current_value": None,
                    "proposed_value": rule,
                    "evidence": {
                        "bucket": bucket["bucket_label"],
                        "cases": cases,
                        "tp": matched["tp"],
                        "sl": matched["sl"],
                        "rapid_sl": rapid,
                        "avg_pnl_pct": avg_pnl,
                        "loss_rate": loss_rate,
                        "baseline_sl_rate": baseline_sl,
                        "sources": sorted(matched["sources"]),
                    },
                })
            ranked.sort(
                key=lambda item: (
                    item["evidence"]["loss_rate"] - item["evidence"]["baseline_sl_rate"],
                    -float(item["evidence"]["avg_pnl_pct"] or 0),
                    item["evidence"]["cases"],
                ),
                reverse=True,
            )
            candidates.extend(ranked[:max_changes])
        return candidates

    async def overview(
        self, db: AsyncSession, user_id: UUID, lookback_days: int = 30
    ) -> dict[str, Any]:
        policy = await self.policy(db, user_id)
        cutoff = datetime.now(timezone.utc)
        rows, truncated = await self._official_rows(
            db, user_id, lookback_days, cutoff,
            int(policy["score_global_max_analysis_rows"]),
        )
        champions = await self._champions(db, user_id)
        by_source = {
            source: _metrics([row for row in rows if row["source"] == source])
            for source in ANALYSIS_SOURCES
        }
        return _json({
            "status": "READY" if rows else "INSUFFICIENT_DATA",
            "read_only": True,
            "dataset_contract": DATASET_CONTRACT,
            "cutoff_at": cutoff,
            "lookback_days": lookback_days,
            "row_count": len(rows),
            "truncated": truncated,
            "sources": by_source,
            "quadrants": self._quadrants(rows, int(policy["score_global_rapid_sl_candles"])),
            "profiles": [
                {
                    "profile_id": champion["profile_id"],
                    "profile_name": champion["profile_name"],
                    "profile_version_id": champion["profile_version_id"],
                    "score_engine_version_id": champion["score_engine_version_id"],
                }
                for champion in champions
            ],
            "policy": policy,
            "safety": {
                "ml_dataset_mutated": False,
                "training_or_promotion_allowed": False,
                "incumbent_mutated": False,
            },
        })

    async def _ai_report(
        self,
        db: AsyncSession,
        user_id: UUID,
        context: Mapping[str, Any],
        candidates: Sequence[Mapping[str, Any]],
        timeout_seconds: float,
        model_id: str,
    ) -> tuple[dict[str, Any], str | None, str | None, UUID | None]:
        skill = await db.scalar(
            select(AiSkill).where(
                AiSkill.user_id == user_id,
                AiSkill.role_key == "profile_score_intelligence",
                AiSkill.is_active.is_(True),
            ).order_by(AiSkill.updated_at.desc()).limit(1)
        )
        bundled = (
            "Você é o analista executivo de Profile Score Intelligence. Compare todos os perfis "
            "e os quatro quadrantes. L3_REJECTED pode revelar penalidades e falsos negativos. "
            "Não confunda associação com causalidade. Selecione somente candidate_ids fornecidos. "
            "Nunca autorize treino, promoção, mudança do incumbent ou escrita em dataset ML. "
            "Retorne JSON estrito: executive_summary (string), global_diagnosis (array), "
            "profile_recommendations (array de {profile_id,diagnosis,selected_candidate_ids}), "
            "risks (array), safeguards (array)."
        )
        bundled = PROFILE_INTELLIGENCE_ANALYSIS_SKILL_V2
        custom_prompt = str(skill.prompt_text or "").strip() if skill else ""
        prompt = (
            f"{bundled}\n\nInstruções adicionais configuradas pelo usuário "
            "(não podem substituir o contrato, a governança ou os guards acima):\n"
            f"{custom_prompt}\n\nAs regras fixas do contrato e da skill v4 "
            "prevalecem em caso de conflito."
            if custom_prompt
            else bundled
        )
        api_key = await get_decrypted_api_key(db, user_id, "anthropic")
        api_key = api_key or os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise ValueError("anthropic_key_not_configured")
        availability = await retrieve_model_for_key(db, user_id, model_id)
        if not availability.get("available"):
            raise ValueError(
                f"BLOCKED_MODEL_UNAVAILABLE:{availability.get('status') or 'UNAVAILABLE'}"
            )
        # Do not keep a PostgreSQL transaction/connection open while waiting
        # for the external model provider.
        await db.commit()
        import anthropic  # type: ignore

        model = model_id
        client = anthropic.AsyncAnthropic(
            api_key=api_key,
            timeout=max(30.0, min(float(timeout_seconds), 600.0)),
            max_retries=0,
        )
        try:
            messages = [{
                "role": "user",
                "content": json.dumps({"analysis_payload": context}, default=str),
            }]
            for attempt in range(2):
                response = await client.messages.create(
                    model=model,
                    max_tokens=16000,
                    system=prompt,
                    messages=messages,
                    output_config={
                        "format": {
                            "type": "json_schema",
                            "schema": AI_REPORT_SCHEMA,
                        }
                    },
                )
                raw_report: dict[str, Any] | None = None
                try:
                    raw_report = _parse_ai_report_response(response)
                    parsed = validate_ai_response_against_payload(
                        raw_report,
                        context,
                    )
                    break
                except ValueError as exc:
                    if attempt > 0:
                        raise
                    if raw_report is not None:
                        messages.append({
                            "role": "assistant",
                            "content": json.dumps(raw_report, default=str),
                        })
                    messages.append({
                        "role": "user",
                        "content": (
                            f"A resposta foi rejeitada pelo guard: {exc}. "
                            "Devolva novamente o JSON completo no schema compacto. "
                            "Use uma frase curta por campo narrativo, não repita "
                            "métricas ou evidências e inclua cada profile_id presente "
                            "em candidates exatamente uma vez em profile_decisions. "
                            "Preserve IDs e selecione somente candidate_ids VALIDATED "
                            "do mesmo profile. Retorne apenas o JSON completo."
                        ),
                    })
        finally:
            await client.close()
        if len(list(parsed.get("executive_summary") or [])) < 4:
            raise ValueError("invalid_profile_score_ai_summary")
        allowed = {item["candidate_id"]: item for item in candidates}
        selected = set(parsed.get("selected_candidate_ids") or [])
        unknown = selected.difference(allowed)
        if unknown:
            raise ValueError("ai_selected_unknown_candidate")
        parsed["selected_candidate_ids"] = sorted(selected)
        return parsed, "anthropic", getattr(response, "model", model), skill.id if skill else None

    async def queue_global_analysis(
        self, db: AsyncSession, user_id: UUID, lookback_days: int = 30,
        idempotency_key: str | None = None,
    ) -> tuple[dict[str, Any], bool]:
        cutoff = datetime.now(timezone.utc)
        policy = await self.policy(db, user_id)
        requested_model = configured_model(policy)
        idem = idempotency_key or (
            f"pi-score-global:{user_id}:{cutoff.isoformat()}"
        )
        existing = await db.scalar(select(ProfileScoreOptimizationRun).where(
            ProfileScoreOptimizationRun.user_id == user_id,
            ProfileScoreOptimizationRun.idempotency_key == idem,
        ))
        if existing:
            return self.public_run(existing), False
        queued_evidence = {
            "dataset_contract": DATASET_CONTRACT,
            "analysis_contract_version": ANALYSIS_CONTRACT_VERSION,
            "analysis_skill_version": ANALYSIS_SKILL_VERSION,
            "ai_model_requested": requested_model,
            "cutoff_at": cutoff,
            "lookback_days": lookback_days,
            "status": "QUEUED",
        }
        run = ProfileScoreOptimizationRun(
            user_id=user_id,
            status="QUEUED",
            lookback_days=lookback_days,
            cutoff_at=cutoff,
            dataset_contract=DATASET_CONTRACT,
            input_hash=_hash({
                "user_id": user_id,
                "lookback_days": lookback_days,
                "cutoff_at": cutoff,
                "idempotency_key": idem,
            }),
            idempotency_key=idem,
            evidence_json=_json(queued_evidence),
            analysis_contract_version=ANALYSIS_CONTRACT_VERSION,
            analysis_skill_version=ANALYSIS_SKILL_VERSION,
            ai_model_requested=requested_model,
        )
        db.add(run)
        await db.flush()
        return self.public_run(run), True

    def _build_analysis(
        self,
        rows: Sequence[Mapping[str, Any]],
        champions: Sequence[Mapping[str, Any]],
        policy: Mapping[str, Any],
        lookback_days: int,
        cutoff: datetime,
        truncated: bool,
    ) -> tuple[dict[str, Any], list[dict[str, Any]], str]:
        deduplicated, deduplication = deduplicate_rows(rows)
        if deduplication["missing_canonical_key_rows"] or truncated:
            approved = [
                row for row in deduplicated if row.get("source") in {"L3", "L3_LAB"}
            ]
            rejected = [
                row for row in deduplicated if row.get("source") == "L3_REJECTED"
            ]
            evidence = {
                "dataset_contract": DATASET_CONTRACT,
                "analysis_contract_version": ANALYSIS_CONTRACT_VERSION,
                "analysis_skill_version": ANALYSIS_SKILL_VERSION,
                "cutoff_at": cutoff,
                "lookback_days": lookback_days,
                "row_count": len(rows),
                "deduplicated_row_count": len(deduplicated),
                "truncated": truncated,
                "deduplication": deduplication,
                "cohorts": {
                    "approved": {
                        "scope": "GLOBAL",
                        "definition": "source in [L3,L3_LAB]",
                        "metrics": cohort_metrics(approved),
                    },
                    "counterfactual_rejected": {
                        "scope": "COUNTERFACTUAL",
                        "definition": "source=L3_REJECTED",
                        "metrics": cohort_metrics(rejected),
                        "profile_attribution_allowed": False,
                    },
                },
                "confusion_matrix": confusion_matrix(deduplicated),
                "source_metrics": {
                    source: cohort_metrics(
                        [row for row in deduplicated if row["source"] == source]
                    )
                    for source in ANALYSIS_SOURCES
                },
                "candidates": [],
                "candidate_count": 0,
                "candidate_accounting": {
                    "candidate_definitions": 0,
                    "profile_rule_applications": 0,
                    "mutation_instances": 0,
                },
                "candidate_applications": [],
                "counterfactual_analysis": None,
                "overlap_analysis": [],
                "policy": policy,
                "safety": {
                    "shadow_only": True,
                    "eligible_for_training": False,
                    "incumbent_mutated": False,
                    "training_or_promotion_allowed": False,
                    "autopilot_invoked": False,
                },
            }
            evidence["pre_ai_validation"] = validate_analysis_payload(evidence)
            return evidence, [], _hash({"evidence": evidence, "candidates": []})
        buckets = list(_get_indicator_buckets())
        feature_cache: dict[str, dict[str, Any]] = {}

        def cached_features(row: Mapping[str, Any]) -> dict[str, Any]:
            key = str(row.get("canonical_trade_key") or row.get("id") or id(row))
            if key not in feature_cache:
                feature_cache[key] = _features(row)
            return feature_cache[key]

        candidates, candidate_summary, applications, counterfactual = build_candidates(
            deduplicated,
            champions,
            policy,
            buckets,
            cached_features,
        )
        overlaps = build_overlap_analysis(
            deduplicated,
            candidates,
            {str(bucket["bucket_label"]): bucket for bucket in buckets},
            cached_features,
        )
        approved = [
            row for row in deduplicated if row.get("source") in {"L3", "L3_LAB"}
        ]
        rejected = [
            row for row in deduplicated if row.get("source") == "L3_REJECTED"
        ]
        evidence = {
            "dataset_contract": DATASET_CONTRACT,
            "analysis_contract_version": ANALYSIS_CONTRACT_VERSION,
            "analysis_skill_version": ANALYSIS_SKILL_VERSION,
            "cutoff_at": cutoff,
            "lookback_days": lookback_days,
            "row_count": len(rows),
            "deduplicated_row_count": len(deduplicated),
            "truncated": truncated,
            "deduplication": deduplication,
            "cohorts": {
                "approved": {
                    "scope": "GLOBAL",
                    "definition": "source in [L3,L3_LAB]",
                    "metrics": cohort_metrics(approved),
                },
                "counterfactual_rejected": {
                    "scope": "COUNTERFACTUAL",
                    "definition": "source=L3_REJECTED",
                    "metrics": cohort_metrics(rejected),
                    "profile_attribution_allowed": False,
                },
            },
            "confusion_matrix": confusion_matrix(deduplicated),
            "source_metrics": {
                source: cohort_metrics(
                    [row for row in deduplicated if row["source"] == source]
                )
                for source in ANALYSIS_SOURCES
            },
            "candidates": candidates,
            "candidate_count": len(candidates),
            "candidate_accounting": candidate_summary,
            "candidate_applications": applications,
            "counterfactual_analysis": counterfactual,
            "overlap_analysis": overlaps,
            "policy": policy,
            "safety": {
                "shadow_only": True,
                "eligible_for_training": False,
                "incumbent_mutated": False,
                "training_or_promotion_allowed": False,
                "autopilot_invoked": False,
            },
        }
        validation = validate_analysis_payload(evidence)
        evidence["pre_ai_validation"] = validation
        return evidence, candidates, _hash({
            "evidence": evidence,
            "candidates": candidates,
        })

    async def process_global_analysis(
        self, db: AsyncSession, run_id: UUID
    ) -> dict[str, Any]:
        claimed = await db.execute(
            update(ProfileScoreOptimizationRun)
            .where(
                ProfileScoreOptimizationRun.id == run_id,
                ProfileScoreOptimizationRun.status == "QUEUED",
            )
            .values(status="ANALYZING", error_code=None)
            .returning(ProfileScoreOptimizationRun.id)
        )
        if claimed.scalar_one_or_none() is None:
            existing = await db.scalar(select(ProfileScoreOptimizationRun).where(
                ProfileScoreOptimizationRun.id == run_id
            ))
            if not existing:
                raise ValueError("optimization_run_not_found")
            return self.public_run(existing)
        await db.commit()

        run = await db.scalar(select(ProfileScoreOptimizationRun).where(
            ProfileScoreOptimizationRun.id == run_id
        ))
        if not run:
            raise ValueError("optimization_run_not_found")
        try:
            policy = await self.policy(db, run.user_id)
            rows, truncated = await self._official_rows(
                db,
                run.user_id,
                run.lookback_days,
                run.cutoff_at,
                int(policy["score_global_max_analysis_rows"]),
            )
            champions = await self._champions(db, run.user_id)
            # Release the read transaction before CPU work.  The fixed cutoff
            # and immutable snapshots preserve the point-in-time contract.
            await db.commit()
            evidence, candidates, input_hash = await asyncio.to_thread(
                self._build_analysis,
                rows,
                champions,
                policy,
                run.lookback_days,
                run.cutoff_at,
                truncated,
            )
            run = await db.scalar(select(ProfileScoreOptimizationRun).where(
                ProfileScoreOptimizationRun.id == run_id
            ))
            if not run:
                raise ValueError("optimization_run_not_found")
            validation = evidence.get("pre_ai_validation") or {}
            if not validation.get("valid"):
                hard_errors = list(validation.get("hard_errors") or [])
                run.status = "ANALYSIS_BLOCKED"
                run.evidence_json = _json(evidence)
                run.input_hash = input_hash
                run.error_code = str(
                    hard_errors[0] if hard_errors else "ANALYSIS_PAYLOAD_INVALID"
                )[:120]
                run.completed_at = datetime.now(timezone.utc)
                await db.commit()
                return self.public_run(run)
            ai_context = build_bounded_ai_context(evidence)
            evidence["ai_context"] = ai_context["bounded_context"]
            run.status = "AI_RUNNING"
            run.evidence_json = _json(evidence)
            run.input_hash = input_hash
            await db.commit()

            report, provider, model, skill_id = await self._ai_report(
                db,
                run.user_id,
                ai_context,
                candidates,
                float(policy["score_global_ai_timeout_seconds"]),
                run.ai_model_requested or configured_model(policy),
            )
            selected = set(report["selected_candidate_ids"])
            changes = [item for item in candidates if item["candidate_id"] in selected]
            envelope = {
                "contract": "profile-score-adjustment-v1",
                "run_id": str(run.id),
                "cutoff_at": run.cutoff_at.isoformat(),
                "base_profiles": [{
                    "profile_id": str(item["profile_id"]),
                    "profile_version_id": str(item["profile_version_id"]),
                    "profile_config_hash": item["config_hash"],
                    "score_engine_version_id": str(item["score_engine_version_id"]),
                    "score_engine_config_hash": item["score_engine_config_hash"],
                } for item in champions],
                "changes": changes,
                "safety": {
                    "shadow_only": True,
                    "eligible_for_training": False,
                    "incumbent_mutated": False,
                    "training_or_promotion_allowed": False,
                },
            }
            run.executive_report = _json(report)
            run.adjustment_envelope = _json(envelope)
            run.provider = provider
            run.model = model
            run.ai_model_effective = model
            run.skill_id = skill_id
            run.status = "AI_COMPLETED"
            run.completed_at = datetime.now(timezone.utc)
        except Exception as exc:
            await db.rollback()
            run = await db.scalar(select(ProfileScoreOptimizationRun).where(
                ProfileScoreOptimizationRun.id == run_id
            ))
            if not run:
                raise
            error_code = str(exc)[:120]
            run.status = (
                "ANALYSIS_BLOCKED_MODEL_UNAVAILABLE"
                if error_code.startswith("BLOCKED_MODEL_UNAVAILABLE")
                else "AI_FAILED"
            )
            run.error_code = error_code
            run.completed_at = datetime.now(timezone.utc)
        await db.commit()
        return self.public_run(run)

    async def replay(
        self, db: AsyncSession, user_id: UUID, run_id: UUID
    ) -> dict[str, Any]:
        run = await db.scalar(select(ProfileScoreOptimizationRun).where(
            ProfileScoreOptimizationRun.id == run_id,
            ProfileScoreOptimizationRun.user_id == user_id,
        ))
        if not run or run.status not in {"AI_COMPLETED", "REPLAY_COMPLETED"}:
            raise ValueError("optimization_run_not_replayable")
        existing_challenger = await db.scalar(
            select(ProfileScoreOptimizationChallenger.id).where(
                ProfileScoreOptimizationChallenger.run_id == run.id
            ).limit(1)
        )
        if existing_challenger:
            raise ValueError("optimization_run_has_active_challenger")
        policy = await self.policy(db, user_id)
        champions = {str(item["profile_id"]): item for item in await self._champions(db, user_id)}
        changes_by_profile: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for change in (run.adjustment_envelope or {}).get("changes") or []:
            changes_by_profile[str(change["profile_id"])].append(change)
        await db.execute(delete(ProfileScoreReplayResult).where(
            ProfileScoreReplayResult.run_id == run.id
        ))
        results = []
        for profile_id, changes in changes_by_profile.items():
            champion = champions.get(profile_id)
            if not champion:
                continue
            if content_hash(champion["config"] or {}) != champion["config_hash"]:
                raise ValueError(f"champion_hash_invalid:{profile_id}")
            candidate = deepcopy(champion["config"] or {})
            for change in changes:
                candidate = apply_manual_action(
                    candidate, change["action_type"], change["target_path"],
                    change.get("current_value"), change.get("proposed_value"),
                )
            rows_result = await db.execute(text(f"""
                SELECT st.id,st.source,st.symbol,st.outcome,st.pnl_pct,
                       st.holding_seconds,st.created_at,st.features_snapshot
                  FROM shadow_trades st
                 WHERE st.user_id=:uid AND st.profile_id=:profile_id
                   AND st.source IN ('L3','L3_LAB','L3_REJECTED')
                   AND st.outcome IN ('TP_HIT','SL_HIT','TIMEOUT')
                   AND st.created_at >= GREATEST(
                       CAST(:window_start AS TIMESTAMPTZ),
                       CAST(:native_capture_start_at AS TIMESTAMPTZ))
                   AND st.created_at <= CAST(:cutoff AS TIMESTAMPTZ)
                   AND {official_where('st')}
                 ORDER BY st.created_at DESC
                 LIMIT CAST(:limit AS INTEGER)
            """), {
                "uid": str(user_id), "profile_id": profile_id,
                "window_start": run.cutoff_at - timedelta(days=run.lookback_days),
                "cutoff": run.cutoff_at,
                "limit": int(policy["score_global_max_analysis_rows"]),
                **official_params(),
            })
            rows = [dict(row) for row in rows_result.mappings().all()]
            champion_selected = [row for row in rows if _evaluate_allow(champion["config"] or {}, row)]
            challenger_selected = [row for row in rows if _evaluate_allow(candidate, row)]
            base_metrics = _metrics(champion_selected)
            next_metrics = _metrics(challenger_selected)
            champion_tp = base_metrics["tp"]
            champion_sl = base_metrics["sl"]
            retained_tp = sum(
                1 for row in champion_selected
                if row["outcome"] == "TP_HIT" and row in challenger_selected
            )
            prevented_sl = sum(
                1 for row in champion_selected
                if row["outcome"] == "SL_HIT" and row not in challenger_selected
            )
            retention = len(challenger_selected) / len(champion_selected) if champion_selected else 0.0
            tp_loss_rate = 1 - retained_tp / champion_tp if champion_tp else 1.0
            sl_reduction = prevented_sl / champion_sl if champion_sl else 0.0
            gates = {
                "min_volume_retention": retention >= float(policy["score_global_replay_min_retention"]),
                "max_tp_loss_rate": tp_loss_rate <= float(policy["score_global_replay_max_tp_loss_rate"]),
                "min_sl_reduction_rate": sl_reduction >= float(policy["score_global_replay_min_sl_reduction_rate"]),
                "positive_ev": (
                    next_metrics["avg_pnl_pct"] is not None
                    and (base_metrics["avg_pnl_pct"] is None
                         or next_metrics["avg_pnl_pct"] >= base_metrics["avg_pnl_pct"])
                ),
                "non_empty": bool(challenger_selected),
            }
            status = "REPLAY_READY" if all(gates.values()) else "REPLAY_BLOCKED"
            delta = {
                "volume_retention": retention,
                "tp_loss_rate": tp_loss_rate,
                "sl_reduction_rate": sl_reduction,
                "prevented_sl": prevented_sl,
                "lost_tp": champion_tp - retained_tp,
                "avg_pnl_delta": (
                    next_metrics["avg_pnl_pct"] - base_metrics["avg_pnl_pct"]
                    if next_metrics["avg_pnl_pct"] is not None and base_metrics["avg_pnl_pct"] is not None
                    else None
                ),
            }
            replay = ProfileScoreReplayResult(
                run_id=run.id, user_id=user_id, profile_id=UUID(profile_id),
                champion_profile_version_id=champion["profile_version_id"],
                champion_score_engine_version_id=champion["score_engine_version_id"],
                candidate_config_hash=content_hash(candidate),
                candidate_config=candidate,
                window_from=run.cutoff_at - timedelta(days=run.lookback_days),
                window_to=run.cutoff_at,
                champion_metrics=_json(base_metrics),
                challenger_metrics=_json(next_metrics),
                delta_metrics=_json(delta),
                gates=gates,
                status=status,
                evidence_hash=_hash({
                    "row_ids": [str(row["id"]) for row in rows],
                    "candidate_config_hash": content_hash(candidate),
                }),
            )
            db.add(replay)
            await db.flush()
            results.append(self.public_replay(replay))
        run.status = "REPLAY_COMPLETED"
        await db.flush()
        return {"run_id": str(run.id), "status": run.status, "items": results}

    async def create_challengers(
        self, db: AsyncSession, user_id: UUID, run_id: UUID
    ) -> dict[str, Any]:
        replays = (await db.execute(select(ProfileScoreReplayResult).where(
            ProfileScoreReplayResult.run_id == run_id,
            ProfileScoreReplayResult.user_id == user_id,
            ProfileScoreReplayResult.status == "REPLAY_READY",
        ))).scalars().all()
        created = []
        policy = await self.policy(db, user_id)
        for replay in replays:
            existing = await db.scalar(select(ProfileScoreOptimizationChallenger).where(
                ProfileScoreOptimizationChallenger.replay_result_id == replay.id
            ))
            if existing:
                created.append(self.public_challenger(existing))
                continue
            active = await db.scalar(select(ProfileScoreOptimizationChallenger).where(
                ProfileScoreOptimizationChallenger.profile_id == replay.profile_id,
                ProfileScoreOptimizationChallenger.status.in_(("CREATED", "COLLECTING", "VALIDATED")),
            ).limit(1))
            if active:
                raise ValueError(f"active_profile_challenger_exists:{active.id}")
            version_id = await create_shadow_profile_version(
                db,
                profile_id=replay.profile_id,
                config=replay.candidate_config,
                cycle_id=run_id,
                origin_profile_id=replay.profile_id,
            )
            gate = {
                key: policy[key] for key in (
                    "score_global_rapid_sl_candles",
                    "score_global_challenger_min_days",
                    "score_global_challenger_min_closed",
                    "score_global_challenger_min_tp",
                    "score_global_challenger_min_sl",
                    "score_global_challenger_min_distinct_symbols",
                    "score_global_challenger_min_distinct_days",
                    "score_global_challenger_max_single_symbol_share",
                    "score_global_challenger_max_single_day_share",
                )
            }
            challenger = ProfileScoreOptimizationChallenger(
                run_id=run_id, replay_result_id=replay.id, user_id=user_id,
                profile_id=replay.profile_id,
                champion_profile_version_id=replay.champion_profile_version_id,
                challenger_profile_version_id=version_id,
                status="COLLECTING", validation_gate=gate,
                collection_started_at=datetime.now(timezone.utc),
            )
            db.add(challenger)
            await db.flush()
            created.append(self.public_challenger(challenger))
        return {"run_id": str(run_id), "created": len(created), "items": created}

    async def performance(
        self, db: AsyncSession, user_id: UUID, profile_id: UUID | None = None
    ) -> dict[str, Any]:
        params = {"uid": str(user_id), "profile_id": str(profile_id) if profile_id else None}
        result = await db.execute(text("""
            SELECT d.metric_date,d.variant,SUM(d.closed_trades)::int AS closed,
                   SUM(d.tp)::int AS tp,SUM(d.sl)::int AS sl,
                   SUM(d.timeout)::int AS timeout,SUM(d.rapid_sl)::int AS rapid_sl,
                   SUM(d.pnl_sum_pct)::double precision AS pnl_sum_pct
              FROM profile_score_performance_daily d
             WHERE d.user_id=:uid
               AND (CAST(:profile_id AS UUID) IS NULL OR d.profile_id=CAST(:profile_id AS UUID))
             GROUP BY d.metric_date,d.variant
             ORDER BY d.metric_date,d.variant
        """), params)
        items = []
        for row in result.mappings().all():
            item = dict(row)
            closed = item["closed"] or 0
            item["tp_rate"] = item["tp"] / closed if closed else None
            item["sl_rate"] = item["sl"] / closed if closed else None
            item["rapid_sl_rate"] = item["rapid_sl"] / closed if closed else None
            items.append(_json(item))
        return {"items": items, "metric_contract": "pi-score-performance-daily-v1"}

    async def refresh_performance(self, db: AsyncSession) -> dict[str, Any]:
        challengers = (await db.execute(select(ProfileScoreOptimizationChallenger).where(
            ProfileScoreOptimizationChallenger.status.in_(("COLLECTING", "VALIDATED"))
        ))).scalars().all()
        updated = 0
        for challenger in challengers:
            await db.execute(delete(ProfileScorePerformanceDaily).where(
                ProfileScorePerformanceDaily.challenger_id == challenger.id
            ))
            result = await db.execute(text("""
                SELECT st.profile_version_id,st.score_engine_version_id,st.source,
                       (st.completed_at AT TIME ZONE 'UTC')::date AS metric_date,
                       COUNT(*)::int AS closed,
                       COUNT(*) FILTER (WHERE st.outcome='TP_HIT')::int AS tp,
                       COUNT(*) FILTER (WHERE st.outcome='SL_HIT')::int AS sl,
                       COUNT(*) FILTER (WHERE st.outcome='TIMEOUT')::int AS timeout,
                       COUNT(*) FILTER (
                           WHERE st.outcome='SL_HIT' AND st.holding_seconds <= :rapid_seconds
                       )::int AS rapid_sl,
                       SUM(st.pnl_pct) AS pnl_sum_pct,AVG(st.pnl_pct) AS avg_pnl_pct,
                       AVG(st.mae_pct) AS avg_mae_pct,AVG(st.mfe_pct) AS avg_mfe_pct,
                       COUNT(DISTINCT st.symbol)::int AS distinct_symbols
                  FROM shadow_trades st
                 WHERE st.user_id=:uid AND st.profile_id=:profile_id
                   AND st.source IN ('PI_CHAMPION_CONTROL','PI_CHALLENGER')
                   AND st.outcome IN ('TP_HIT','SL_HIT','TIMEOUT')
                   AND st.completed_at IS NOT NULL
                   AND (
                       (st.source='PI_CHAMPION_CONTROL' AND st.profile_version_id=:champion_version)
                       OR
                       (st.source='PI_CHALLENGER' AND st.profile_version_id=:challenger_version)
                   )
                 GROUP BY st.profile_version_id,st.score_engine_version_id,st.source,
                          (st.completed_at AT TIME ZONE 'UTC')::date
            """), {
                "uid": str(challenger.user_id), "profile_id": str(challenger.profile_id),
                "champion_version": str(challenger.champion_profile_version_id),
                "challenger_version": str(challenger.challenger_profile_version_id),
                "rapid_seconds": int((challenger.validation_gate or {}).get(
                    "score_global_rapid_sl_candles", 12
                )) * 300,
            })
            for row in result.mappings().all():
                variant = "champion" if row["source"] == CHAMPION_SOURCE else "challenger"
                db.add(ProfileScorePerformanceDaily(
                    challenger_id=challenger.id, user_id=challenger.user_id,
                    profile_id=challenger.profile_id,
                    profile_version_id=row["profile_version_id"],
                    score_engine_version_id=row["score_engine_version_id"],
                    variant=variant, source=row["source"], metric_date=row["metric_date"],
                    closed_trades=row["closed"], tp=row["tp"], sl=row["sl"],
                    timeout=row["timeout"], rapid_sl=row["rapid_sl"],
                    pnl_sum_pct=row["pnl_sum_pct"], avg_pnl_pct=row["avg_pnl_pct"],
                    avg_mae_pct=row["avg_mae_pct"], avg_mfe_pct=row["avg_mfe_pct"],
                    distinct_symbols=row["distinct_symbols"],
                ))
                updated += 1
            gate_result = await db.execute(text("""
                WITH base AS (
                    SELECT st.source,st.outcome,st.symbol,
                           (st.completed_at AT TIME ZONE 'UTC')::date AS day,
                           st.holding_seconds
                      FROM shadow_trades st
                     WHERE st.user_id=:uid AND st.profile_id=:profile_id
                       AND st.source IN ('PI_CHAMPION_CONTROL','PI_CHALLENGER')
                       AND st.outcome IN ('TP_HIT','SL_HIT','TIMEOUT')
                       AND st.completed_at IS NOT NULL
                       AND st.created_at >= :started_at
                       AND (
                           (st.source='PI_CHAMPION_CONTROL' AND st.profile_version_id=:champion_version)
                           OR
                           (st.source='PI_CHALLENGER' AND st.profile_version_id=:challenger_version)
                       )
                ), counts AS (
                    SELECT source,COUNT(*)::int AS closed,
                           COUNT(*) FILTER (WHERE outcome='TP_HIT')::int AS tp,
                           COUNT(*) FILTER (WHERE outcome='SL_HIT')::int AS sl,
                           COUNT(*) FILTER (
                               WHERE outcome='SL_HIT' AND holding_seconds <= :rapid_seconds
                           )::int AS rapid_sl,
                           COUNT(DISTINCT symbol)::int AS distinct_symbols,
                           COUNT(DISTINCT day)::int AS distinct_days
                      FROM base GROUP BY source
                ), symbol_share AS (
                    SELECT source,MAX(n)::double precision / NULLIF(SUM(n),0) AS max_share
                      FROM (SELECT source,symbol,COUNT(*) AS n FROM base GROUP BY source,symbol) q
                     GROUP BY source
                ), day_share AS (
                    SELECT source,MAX(n)::double precision / NULLIF(SUM(n),0) AS max_share
                      FROM (SELECT source,day,COUNT(*) AS n FROM base GROUP BY source,day) q
                     GROUP BY source
                )
                SELECT c.*,s.max_share AS max_symbol_share,d.max_share AS max_day_share
                  FROM counts c
                  LEFT JOIN symbol_share s USING(source)
                  LEFT JOIN day_share d USING(source)
            """), {
                "uid": str(challenger.user_id), "profile_id": str(challenger.profile_id),
                "champion_version": str(challenger.champion_profile_version_id),
                "challenger_version": str(challenger.challenger_profile_version_id),
                "started_at": challenger.collection_started_at,
                "rapid_seconds": int((challenger.validation_gate or {}).get(
                    "score_global_rapid_sl_candles", 12
                )) * 300,
            })
            aggregate = {
                str(row["source"]): dict(row)
                for row in gate_result.mappings().all()
            }
            champ = aggregate.get(CHAMPION_SOURCE, {})
            chall = aggregate.get(CHALLENGER_SOURCE, {})
            gate = dict(challenger.validation_gate or {})
            elapsed_days = max(
                0, (datetime.now(timezone.utc) - challenger.collection_started_at).days
            )
            support_checks = {
                "min_days": elapsed_days >= int(gate["score_global_challenger_min_days"]),
                "min_closed": int(chall.get("closed") or 0) >= int(gate["score_global_challenger_min_closed"]),
                "min_tp": int(chall.get("tp") or 0) >= int(gate["score_global_challenger_min_tp"]),
                "min_sl": int(chall.get("sl") or 0) >= int(gate["score_global_challenger_min_sl"]),
                "distinct_symbols": int(chall.get("distinct_symbols") or 0) >= int(gate["score_global_challenger_min_distinct_symbols"]),
                "distinct_days": int(chall.get("distinct_days") or 0) >= int(gate["score_global_challenger_min_distinct_days"]),
                "max_single_symbol_share": float(chall.get("max_symbol_share") or 1) <= float(gate["score_global_challenger_max_single_symbol_share"]),
                "max_single_day_share": float(chall.get("max_day_share") or 1) <= float(gate["score_global_challenger_max_single_day_share"]),
            }
            if all(support_checks.values()):
                champion_closed = int(champ.get("closed") or 0)
                challenger_closed = int(chall.get("closed") or 0)
                champion_tp_rate = int(champ.get("tp") or 0) / max(champion_closed, 1)
                challenger_tp_rate = int(chall.get("tp") or 0) / max(challenger_closed, 1)
                champion_sl_rate = int(champ.get("sl") or 0) / max(champion_closed, 1)
                challenger_sl_rate = int(chall.get("sl") or 0) / max(challenger_closed, 1)
                champion_rapid_rate = int(champ.get("rapid_sl") or 0) / max(champion_closed, 1)
                challenger_rapid_rate = int(chall.get("rapid_sl") or 0) / max(challenger_closed, 1)
                comparison = {
                    "tp_rate_not_worse": challenger_tp_rate >= champion_tp_rate,
                    "sl_rate_reduced": challenger_sl_rate < champion_sl_rate,
                    "rapid_sl_rate_not_worse": challenger_rapid_rate <= champion_rapid_rate,
                }
                challenger.validation_gate = {
                    **gate, "support_checks": support_checks,
                    "comparison_checks": comparison,
                    "champion": _json(champ), "challenger": _json(chall),
                }
                if all(comparison.values()):
                    challenger.status = "VALIDATED"
                    challenger.validated_at = datetime.now(timezone.utc)
                else:
                    challenger.status = "BLOCKED"
                    challenger.failure_reason = ",".join(
                        key for key, passed in comparison.items() if not passed
                    )
        await db.flush()
        return {"challengers": len(challengers), "daily_rows": updated}

    async def get_run(
        self, db: AsyncSession, user_id: UUID, run_id: UUID
    ) -> dict[str, Any]:
        run = await db.scalar(select(ProfileScoreOptimizationRun).where(
            ProfileScoreOptimizationRun.id == run_id,
            ProfileScoreOptimizationRun.user_id == user_id,
        ))
        if not run:
            raise ValueError("optimization_run_not_found")
        payload = self.public_run(run)
        replays = (await db.execute(select(ProfileScoreReplayResult).where(
            ProfileScoreReplayResult.run_id == run.id
        ))).scalars().all()
        challengers = (await db.execute(select(ProfileScoreOptimizationChallenger).where(
            ProfileScoreOptimizationChallenger.run_id == run.id
        ))).scalars().all()
        payload["replays"] = [self.public_replay(item) for item in replays]
        payload["challengers"] = [self.public_challenger(item) for item in challengers]
        return payload

    async def list_runs(
        self, db: AsyncSession, user_id: UUID, limit: int = 20
    ) -> list[dict[str, Any]]:
        rows = (await db.execute(select(ProfileScoreOptimizationRun).where(
            ProfileScoreOptimizationRun.user_id == user_id
        ).order_by(ProfileScoreOptimizationRun.created_at.desc()).limit(limit))).scalars().all()
        return [self.public_run(row, compact=True) for row in rows]

    @staticmethod
    def public_run(row: ProfileScoreOptimizationRun, compact: bool = False) -> dict[str, Any]:
        payload = {
            "id": str(row.id), "status": row.status, "lookback_days": row.lookback_days,
            "cutoff_at": row.cutoff_at.isoformat(), "dataset_contract": row.dataset_contract,
            "input_hash": row.input_hash, "provider": row.provider, "model": row.model,
            "analysis_contract_version": row.analysis_contract_version,
            "analysis_skill_version": row.analysis_skill_version,
            "ai_model_requested": row.ai_model_requested,
            "ai_model_effective": row.ai_model_effective,
            "skill_id": str(row.skill_id) if row.skill_id else None,
            "error_code": row.error_code,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "completed_at": row.completed_at.isoformat() if row.completed_at else None,
        }
        if not compact:
            payload.update({
                "evidence": row.evidence_json,
                "executive_report": row.executive_report,
                "adjustment_envelope": row.adjustment_envelope,
            })
        return _json(payload)

    @staticmethod
    def public_replay(row: ProfileScoreReplayResult) -> dict[str, Any]:
        return _json({
            "id": row.id, "profile_id": row.profile_id, "status": row.status,
            "champion_profile_version_id": row.champion_profile_version_id,
            "champion_score_engine_version_id": row.champion_score_engine_version_id,
            "candidate_config_hash": row.candidate_config_hash,
            "champion_metrics": row.champion_metrics,
            "challenger_metrics": row.challenger_metrics,
            "delta_metrics": row.delta_metrics, "gates": row.gates,
            "evidence_hash": row.evidence_hash,
        })

    @staticmethod
    def public_challenger(row: ProfileScoreOptimizationChallenger) -> dict[str, Any]:
        return _json({
            "id": row.id, "run_id": row.run_id, "profile_id": row.profile_id,
            "champion_profile_version_id": row.champion_profile_version_id,
            "challenger_profile_version_id": row.challenger_profile_version_id,
            "status": row.status, "validation_gate": row.validation_gate,
            "collection_started_at": row.collection_started_at,
            "validated_at": row.validated_at, "failure_reason": row.failure_reason,
        })


profile_score_optimization_service = ProfileScoreOptimizationService()
