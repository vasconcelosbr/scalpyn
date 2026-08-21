from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..ai_orchestration.configuration_bundle_service import ConfigurationBundleService
from ..ai_orchestration.context_memory import ContextFingerprint, mutation_fingerprint
from ..ai_orchestration.hashing import canonical_hash
from ..ai_orchestration.contracts import (
    AIRequest, AIRequestIntent, AnalysisMode, Authority, CanonicalDatasetRequest,
    ConfigurationScope, ProviderModelRequest,
)
from ..ai_orchestration.dataset_service import CanonicalDatasetService
from ..ai_orchestration.initial_prompts import initial_prompt_registry
from ..ai_orchestration.job_service import LeaseJob
from ..ai_orchestration.langgraph.config import get_langgraph_settings
from ..ai_orchestration.module_registry import module_capability_registry
from ..ai_orchestration.multimodule_contracts import AnalysisContextManifest, ModuleContextRefs
from ..ai_orchestration.persistence import SQLAlchemyAIPersistence
from ..ai_orchestration.provider_registry import default_registry
from ..models.config_profile import ConfigProfile
from ..models.profile import Profile
from ..models.profile_intelligence import MLModelRegistry
from ..models.shadow_trade import ShadowTrade
from ..models.social_intelligence import SocialAssetObservation
from ..models.systemic_ai import AIModelApprovalRecord
from .ai_graph_service import AIGraphRunService
from .profile_intelligence_live_service import _INDICATOR_NAMES


_READ_ONLY_MODULES = {
    "ml_models",
    "global_risk",
    "strategies",
    "intelligence_runs",
    "social_score",
    "market_regime",
    "audit_version_memory",
}
_REGENERATIVE_MODULES = {"strategy_profiles", "score_engine", "shadow_portfolio"}
_DOMAIN = {
    "strategy_profiles": "STRATEGY_PROFILES",
    "ml_models": "ML_BAYESIAN",
    "shadow_portfolio": "SHADOW_PORTFOLIO",
    "score_engine": "SCORE_ENGINE",
    "global_risk": "GLOBAL_RISK",
    "strategies": "STRATEGIES",
    "intelligence_runs": "INTELLIGENCE_RUNS",
    "social_score": "SOCIAL_SCORE",
    "market_regime": "MARKET_REGIME",
    "audit_version_memory": "AUDIT_VERSION_MEMORY",
}
_CONFIG_TYPES = {
    "score_engine": ("score", "score_engine"),
    "global_risk": ("risk",),
    "strategies": ("strategies", "strategy", "spot_engine"),
}
# Training-internals keys inside MLModelRegistry.metrics_json that carry no
# entry-indicator signal (raw Optuna trial log, threshold-calibration sweep,
# train/test/validation split bookkeeping) but historically dominated the
# evidence payload's byte count -- confirmed 2026-08-17 against a live
# root-cause-audit request: these three keys were ~159KB of a 359KB metrics
# total across 20 models, while the one indicator-relevant key
# (intelligence_report) was untouched by this filter.
_ML_METRICS_TRAINING_INTERNAL_KEYS = frozenset({
    "optuna_study", "threshold_curve", "split_diagnostics",
})


def _ml_metrics_for_evidence(metrics_json: Any) -> Any:
    if not isinstance(metrics_json, dict):
        return metrics_json
    return {
        key: value for key, value in metrics_json.items()
        if key not in _ML_METRICS_TRAINING_INTERNAL_KEYS
    }


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _pct_change(after: float | None, entry: float | None) -> float | None:
    """% change of a post-exit reference price relative to entry_price.

    Mirrors the inline CASE expressions in shadow_trades.py's
    shadow_trades_timeout_analysis (avg_chg_1h/2h/4h/12h/24h) -- computed
    once per row here instead of shipping raw price_after_* columns, since
    only the delta is ever consumed downstream.
    """
    if after is None or entry is None or entry == 0:
        return None
    return (after - entry) / entry * 100


def _uuid_or_none(value: Any) -> UUID | None:
    try:
        return UUID(str(value)) if value else None
    except (TypeError, ValueError):
        return None


def _filter_window_bound(value: Any) -> datetime | None:
    """Parse an explicit window_start/window_end filter value.

    FIX-AC-GOV-002 Fase 7.2: an implicit "most recent N rows" cut is a
    chronological sample by construction -- it cannot be compared against a
    different period without silently confounding recency with whatever
    the analysis is trying to measure. An explicit window makes the period
    a caller decision, not an accident of query order.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


class ModuleAIAnalysisService:
    @staticmethod
    async def _rows(
        db: AsyncSession, *, tenant_id: UUID, module_key: str,
        entity_ids: tuple[str, ...], filters: dict[str, Any],
    ) -> list[dict[str, Any]]:
        limit = min(max(int(filters.get("max_rows", 200)), 1), 5_000)
        ids: list[UUID] = []
        for raw in entity_ids:
            try:
                ids.append(UUID(raw))
            except ValueError:
                continue

        if module_key == "strategy_profiles":
            statement = select(Profile).where(Profile.user_id == tenant_id)
            if ids:
                statement = statement.where(Profile.id.in_(ids))
            records = list((await db.execute(statement.order_by(Profile.updated_at.desc()).limit(limit))).scalars())
            version_rows = []
            if records:
                version_rows = (await db.execute(text("""
                    SELECT DISTINCT ON (profile_id)
                           profile_id, id, version_number, config_hash,
                           score_engine_version_id, parent_version_id, status, created_at
                      FROM profile_versions
                     WHERE profile_id = ANY(CAST(:profile_ids AS uuid[]))
                     ORDER BY profile_id, version_number DESC
                """), {"profile_ids": [str(row.id) for row in records]})).mappings().all()
            versions = {str(row["profile_id"]): row for row in version_rows}
            return [{
                "id": str(row.id), "event_identity": str(row.id), "outcome": "CONFIGURATION",
                "lineage_status": "VERSIONED" if str(row.id) in versions else "LEGACY",
                "profile_id": str(row.id), "profile_name": row.name,
                "profile_family": row.profile_type,
                "timeframe": (row.config or {}).get("default_timeframe"),
                "profile_version_id": str(versions[str(row.id)]["id"]) if str(row.id) in versions else None,
                "version_number": versions[str(row.id)]["version_number"] if str(row.id) in versions else None,
                "config_hash": (
                    versions[str(row.id)]["config_hash"] if str(row.id) in versions
                    else canonical_hash(row.config or {})
                ),
                "score_engine_version_id": (
                    str(versions[str(row.id)]["score_engine_version_id"])
                    if str(row.id) in versions and versions[str(row.id)]["score_engine_version_id"]
                    else None
                ),
                "parent_version_id": (
                    str(versions[str(row.id)]["parent_version_id"])
                    if str(row.id) in versions and versions[str(row.id)]["parent_version_id"]
                    else None
                ),
                "version_status": versions[str(row.id)]["status"] if str(row.id) in versions else None,
                # "config" is intentionally not echoed whole here: signals/
                # entry_triggers/block_rules/filters below already extract
                # every key from it except scoring/default_timeframe, so
                # including it too just doubles ~67KB/request of duplicate
                # JSON with zero new information (confirmed byte-identical
                # against a live evidence-payload audit, 2026-08-17).
                "scoring": (row.config or {}).get("scoring"),
                "signals": (row.config or {}).get("signals"),
                "entry_triggers": (row.config or {}).get("entry_triggers"),
                "block_rules": (row.config or {}).get("block_rules"),
                "filters": (row.config or {}).get("filters"),
                "is_active": bool(row.is_active),
                "is_shadow_only": bool(row.is_shadow_only),
                "live_trading_enabled": bool(row.live_trading_enabled),
                "created_at": _iso(row.created_at),
                "updated_at": _iso(row.updated_at),
            } for row in records]

        if module_key == "ml_models":
            # ml_model_registry.profile_id is nullable by design -- a NULL
            # marks a global-scope model trained across every profile, not an
            # ownership gap (ml_challenger_service.py: "L3 has 66%+ NULL
            # profile_id"). An inner join here silently excludes every
            # global-scope model, which in practice today is 100% of them:
            # ml_model_registry has never contained a single non-NULL
            # profile_id row. Outer join + OR keeps profile-scoped models
            # correctly tenant-filtered while still surfacing global ones.
            statement = select(MLModelRegistry).outerjoin(
                Profile, Profile.id == MLModelRegistry.profile_id,
            ).where(
                or_(MLModelRegistry.profile_id.is_(None), Profile.user_id == tenant_id)
            )
            status_in = filters.get("status_in")
            if status_in:
                statement = statement.where(MLModelRegistry.status.in_(status_in))
            if ids:
                statement = statement.where(MLModelRegistry.model_id.in_(ids))
            records = list((await db.execute(
                statement.order_by(MLModelRegistry.created_at.desc()).limit(limit)
            )).scalars())
            return [{
                "id": str(row.model_id), "event_identity": str(row.model_id), "outcome": row.status,
                "lineage_status": "VERSIONED", "model_type": row.model_type,
                "model_version": row.model_version, "profile_id": str(row.profile_id),
                "model_lane": row.strategy_skill, "market_regime": row.market_regime,
                "dataset_version": row.dataset_version,
                "feature_contract": row.feature_schema_version,
                "label_contract": row.label_version,
                "metrics": _ml_metrics_for_evidence(row.metrics_json),
                "train_start": _iso(row.train_start), "train_end": _iso(row.train_end),
            } for row in records]

        if module_key == "shadow_portfolio":
            statement = select(ShadowTrade).where(ShadowTrade.user_id == tenant_id)
            if ids:
                statement = statement.where(ShadowTrade.id.in_(ids))
            window_start = _filter_window_bound(filters.get("window_start"))
            window_end = _filter_window_bound(filters.get("window_end"))
            if window_start is not None:
                statement = statement.where(ShadowTrade.entry_timestamp >= window_start)
            if window_end is not None:
                statement = statement.where(ShadowTrade.entry_timestamp <= window_end)
            sampling_method = str(filters.get("sampling_method") or "recent")
            if sampling_method == "random":
                # Uniform random draw from the (optionally windowed) population,
                # not a proxy for "most recent" -- required whenever the
                # analysis is comparing a rate against a baseline rather than
                # inspecting the latest activity.
                statement = statement.order_by(func.random()).limit(limit)
            elif sampling_method == "recent":
                statement = statement.order_by(ShadowTrade.entry_timestamp.desc()).limit(limit)
            else:
                raise ValueError(f"Unsupported sampling_method: {sampling_method}")
            records = list((await db.execute(statement)).scalars())
            return [{
                "id": str(row.id), "event_identity": str(row.event_id or row.id),
                "outcome": row.outcome or row.status, "lineage_status": (
                    "CANONICAL" if row.event_id and row.profile_version_id and row.score_engine_version_id
                    else "UNRESOLVED"
                ),
                "symbol": row.symbol, "profile_id": str(row.profile_id) if row.profile_id else None,
                "profile_version_id": str(row.profile_version_id) if row.profile_version_id else None,
                "score_engine_version_id": str(row.score_engine_version_id) if row.score_engine_version_id else None,
                "feature_contract": row.feature_schema_version,
                "label_contract": row.label_contract_version,
                "entry_timestamp": _iso(row.entry_timestamp), "exit_timestamp": _iso(row.exit_timestamp),
                "status": row.status, "pnl_pct": float(row.pnl_pct) if row.pnl_pct is not None else None,
                # Fields below back the ranked shadow_portfolio.get_* handlers
                # (module_tool_runtime.py) -- kept as small scalars, never as
                # nested blobs, so the 10 named tools can compute real
                # per-metric aggregates without re-querying the database.
                "pnl_usdt": float(row.pnl_usdt) if row.pnl_usdt is not None else None,
                "holding_seconds": row.holding_seconds,
                "mae_pct": row.mae_pct, "mfe_pct": row.mfe_pct,
                "delayed_tp": row.delayed_tp, "delayed_tp_hours": row.delayed_tp_hours,
                "timeout_post_analysis_done": bool(row.timeout_post_analysis_done),
                "max_profit_after_timeout_pct": row.max_profit_after_timeout_pct,
                "max_drawdown_after_timeout_pct": row.max_drawdown_after_timeout_pct,
                "horizon_change_pct": {
                    horizon: _pct_change(getattr(row, f"price_after_{horizon}"), row.entry_price)
                    for horizon in ("1h", "2h", "4h", "12h", "24h")
                },
                "final_score": (row.config_snapshot or {}).get("final_score"),
                # Curated scalar subset of features_snapshot (never the full blob --
                # see module docstring: "kept as small scalars, never as nested
                # blobs"). Same 15 indicators as the profile_indicator_performance
                # miner (profile_intelligence_live_service.py:_INDICATOR_NAMES),
                # reused rather than redefined so the two don't silently diverge.
                # Backs shadow.get_indicator_lift.
                **{
                    f"indicator_{name}": (row.features_snapshot or {}).get(name)
                    for name in _INDICATOR_NAMES
                },
                "features_coverage": float(row.features_coverage) if row.features_coverage is not None else None,
                "market_data_confidence": (
                    float(row.market_data_confidence) if row.market_data_confidence is not None else None
                ),
                "oldest_indicator_age_s": row.oldest_indicator_age_s,
                "eligible_for_training": bool(row.eligible_for_training),
            } for row in records]

        if module_key in _CONFIG_TYPES:
            records = list((await db.execute(select(ConfigProfile).where(
                ConfigProfile.user_id == tenant_id,
                ConfigProfile.config_type.in_(_CONFIG_TYPES[module_key]),
                ConfigProfile.is_active.is_(True),
            ).order_by(ConfigProfile.updated_at.desc()).limit(limit))).scalars())
            return [{
                "id": str(row.id), "event_identity": str(row.id), "outcome": "ACTIVE_CONFIGURATION",
                "lineage_status": "VERSIONED_BY_AUDIT", "config_type": row.config_type,
                "config": row.config_json, "updated_at": _iso(row.updated_at),
            } for row in records]

        if module_key == "social_score":
            allowed_symbols = set((await db.execute(select(ShadowTrade.symbol).where(
                ShadowTrade.user_id == tenant_id,
            ).distinct().limit(500))).scalars())
            requested_symbols = {value.upper() for value in entity_ids if "-" not in value}
            symbols = allowed_symbols & requested_symbols if requested_symbols else allowed_symbols
            if not symbols:
                return []
            records = list((await db.execute(select(SocialAssetObservation).where(
                SocialAssetObservation.symbol.in_(symbols),
            ).order_by(SocialAssetObservation.window_end.desc()).limit(limit))).scalars())
            return [{
                "id": str(row.id), "event_identity": str(row.id), "outcome": "OBSERVATION",
                "lineage_status": "CANONICAL", "symbol": row.symbol,
                "window_start": _iso(row.window_start), "window_end": _iso(row.window_end),
                "sources": row.sources, "mentions": (row.metrics or {}).get("mentions"),
                "sentiment": row.sentiment_score, "trend": (row.metrics or {}).get("trend"),
                "confidence": row.confidence, "coverage": (row.metrics or {}).get("coverage"),
                "missingness": (row.metrics or {}).get("missingness") or [],
                "anomaly_flags": row.anomalies, "contract_version": row.contract_version,
            } for row in records]

        if module_key == "market_regime":
            records = (await db.execute(text("""
                SELECT id, regime, confidence, source, indicators_snapshot, detected_at
                  FROM regime_history
                 ORDER BY detected_at DESC
                 LIMIT :limit
            """), {"limit": limit})).mappings().all()
            return [{
                "id": str(row["id"]), "event_identity": str(row["id"]),
                "outcome": "REGIME_OBSERVATION", "lineage_status": "CANONICAL",
                "regime": row["regime"], "confidence": row["confidence"],
                "source": row["source"], "features": row["indicators_snapshot"],
                "detected_at": _iso(row["detected_at"]),
            } for row in records]

        if module_key == "intelligence_runs":
            records = (await db.execute(text("""
                SELECT id, graph_definition_id, status, current_node, authority,
                       terminal_reason, created_at, completed_at
                  FROM ai_graph_runs
                 WHERE tenant_id = :tenant_id
                 ORDER BY created_at DESC
                 LIMIT :limit
            """), {"tenant_id": tenant_id, "limit": limit})).mappings().all()
            return [{
                "id": str(row["id"]), "event_identity": str(row["id"]),
                "outcome": row["status"], "lineage_status": "CANONICAL",
                "graph_definition_id": str(row["graph_definition_id"]),
                "current_node": row["current_node"], "authority": row["authority"],
                "terminal_reason": row["terminal_reason"],
                "created_at": _iso(row["created_at"]), "completed_at": _iso(row["completed_at"]),
            } for row in records]

        if module_key == "audit_version_memory":
            records = (await db.execute(text("""
                SELECT id, ai_request_id, dataset_snapshot_id, configuration_bundle_id,
                       authority, status, payload, mutation_fingerprint,
                       context_fingerprint, context_json, created_at
                  FROM decision_memory
                 WHERE tenant_id = :tenant_id
                 ORDER BY created_at DESC
                 LIMIT :limit
            """), {"tenant_id": tenant_id, "limit": limit})).mappings().all()
            return [{
                "id": str(row["id"]), "event_identity": str(row["id"]),
                "outcome": row["status"], "lineage_status": "CANONICAL",
                "ai_request_id": str(row["ai_request_id"]) if row["ai_request_id"] else None,
                "dataset_snapshot_id": str(row["dataset_snapshot_id"]) if row["dataset_snapshot_id"] else None,
                "configuration_bundle_id": (
                    str(row["configuration_bundle_id"]) if row["configuration_bundle_id"] else None
                ),
                "authority": row["authority"], "payload": row["payload"],
                "mutation_fingerprint": row["mutation_fingerprint"],
                "context_fingerprint": row["context_fingerprint"],
                "context": row["context_json"], "created_at": _iso(row["created_at"]),
            } for row in records]

        return []

    @staticmethod
    def _graph_key(mode: AnalysisMode) -> str:
        if mode is AnalysisMode.ROOT_CAUSE_AUDIT:
            return "root-cause-audit-v2"
        if mode is AnalysisMode.REGENERATIVE:
            return "regenerative-shadow-v2"
        return "systemic-analysis-v2"

    @classmethod
    async def create_run(
        cls, db: AsyncSession, *, tenant_id: UUID, user_id: UUID,
        origin_module: str, origin_view: str, entity_ids: tuple[str, ...],
        filters: dict[str, Any], analysis_mode: AnalysisMode, question: str,
        authority: Authority, provider: str, model: str, model_approval_id: UUID,
        idempotency_key: str, graph_key_override: str | None = None,
        analysis_prompt_version_id: UUID | None = None,
        analysis_prompt_binding: dict[str, Any] | None = None,
    ):
        settings = get_langgraph_settings()
        settings.require_runtime()
        if not settings.entrypoints_enabled:
            raise RuntimeError("LANGGRAPH_ENTRYPOINTS_DISABLED")
        settings.require_module(origin_module)
        capability = module_capability_registry.get(origin_module)
        if capability is None:
            raise RuntimeError("AI_MODULE_UNKNOWN")
        if origin_module in _READ_ONLY_MODULES and authority is not Authority.ANALYSIS_ONLY:
            raise RuntimeError("AI_MODULE_READ_ONLY")
        if analysis_mode is AnalysisMode.REGENERATIVE:
            if origin_module not in _REGENERATIVE_MODULES:
                raise RuntimeError("AI_MODULE_REGENERATIVE_DENIED")
            if authority is not Authority.SHADOW_ONLY:
                raise RuntimeError("AI_REGENERATIVE_SHADOW_AUTHORITY_REQUIRED")
            if not settings.regenerative_shadow_enabled:
                raise RuntimeError("LANGGRAPH_REGENERATIVE_SHADOW_DISABLED")
        if graph_key_override not in {None, "copilot-systemic-v2"}:
            raise RuntimeError("AI_GRAPH_OVERRIDE_DENIED")
        if graph_key_override == "copilot-systemic-v2" and not origin_view.startswith("copilot"):
            raise RuntimeError("AI_COPILOT_GRAPH_ORIGIN_DENIED")

        approval = await db.get(AIModelApprovalRecord, model_approval_id)
        now = datetime.now(timezone.utc)
        if (
            approval is None
            or approval.tenant_id != tenant_id
            or approval.approved_by != user_id
            or approval.provider != provider
            or approval.model != model
            or approval.scope != "SYSTEMIC_MODULE_ANALYSIS"
            or approval.status != "APPROVED"
            or approval.expires_at <= now
        ):
            raise RuntimeError("MODEL_COST_APPROVAL_INVALID")

        rows = await cls._rows(
            db, tenant_id=tenant_id, module_key=origin_module,
            entity_ids=entity_ids, filters=filters,
        )
        if not rows:
            raise RuntimeError("AI_MODULE_DATASET_EMPTY")
        window_start = now - timedelta(microseconds=1)
        timestamps = []
        for row in rows:
            for key in ("entry_timestamp", "window_start", "train_start", "updated_at"):
                if row.get(key):
                    try:
                        timestamps.append(datetime.fromisoformat(str(row[key])))
                    except ValueError:
                        pass
        if timestamps:
            window_start = min(timestamps)
        if window_start >= now:
            window_start = now - timedelta(microseconds=1)

        dependencies = tuple(
            value for value in capability.dependencies if value in module_capability_registry
        )
        tool_allowlist = list(capability.read_tools)
        for dependency in dependencies:
            dependency_tools = module_capability_registry[dependency].read_tools
            if dependency_tools:
                tool_allowlist.append(dependency_tools[0])
        for veto_tool in (
            "global_risk.validate_recommendation",
            "strategies.validate_recommendation",
        ):
            if any(veto_tool in item.read_tools for item in module_capability_registry.values()):
                tool_allowlist.append(veto_tool)
        tool_allowlist = list(dict.fromkeys(tool_allowlist))
        context_manifest = AnalysisContextManifest(
            modules_requested=(origin_module,), modules_consulted=(origin_module, *dependencies),
            tools_called=(), freshness={origin_module: capability.freshness_sla_seconds},
            quality={origin_module: "PENDING_GATE"},
            evidence_ids=tuple(str(row["id"]) for row in rows),
        )
        policy_records = list((await db.execute(select(ConfigProfile).where(
            ConfigProfile.user_id == tenant_id,
            ConfigProfile.config_type.in_((
                "risk", "strategies", "strategy", "spot_engine", "score", "score_engine",
            )),
            ConfigProfile.is_active.is_(True),
        ).order_by(ConfigProfile.updated_at.desc()))).scalars())
        policies: dict[str, ConfigProfile] = {}
        for record in policy_records:
            policies.setdefault(record.config_type, record)
        first_row = rows[0]
        profile_id = _uuid_or_none(first_row.get("profile_id"))
        profile_version_id = _uuid_or_none(first_row.get("profile_version_id"))
        score_engine_version_id = _uuid_or_none(first_row.get("score_engine_version_id"))
        ml_model_id = _uuid_or_none(first_row.get("id")) if origin_module == "ml_models" else None
        market_regime_id = await db.scalar(text("""
            SELECT id FROM regime_history ORDER BY detected_at DESC LIMIT 1
        """))
        strategy_policy = policies.get("strategies") or policies.get("strategy")
        spot_policy = policies.get("spot_engine")
        bundle_json = {
            "origin_module": origin_module,
            "entity_ids": list(entity_ids),
            "configuration_rows": [{
                "id": str(record.id), "config_type": record.config_type,
                "config": record.config_json, "updated_at": _iso(record.updated_at),
            } for record in policy_records],
            "live_write": False,
            "model_approval_id": str(model_approval_id),
            "model_max_cost_usd": str(approval.max_cost_usd),
        }
        bundle = ConfigurationBundleService.create(
            tenant_id=tenant_id,
            bundle_json=bundle_json,
            profile_id=profile_id,
            profile_version_id=profile_version_id,
            score_engine_version_id=score_engine_version_id,
            risk_policy_version_id=policies.get("risk").id if policies.get("risk") else None,
            strategy_policy_version_id=strategy_policy.id if strategy_policy else None,
            spot_policy_version_id=spot_policy.id if spot_policy else None,
            exit_policy_version_id=strategy_policy.id if strategy_policy else None,
            feature_contract_version=first_row.get("feature_contract"),
            label_contract_version=first_row.get("label_contract"),
            ml_model_id=ml_model_id,
            model_lane=first_row.get("model_lane"),
            market_regime_id=_uuid_or_none(market_regime_id),
        )
        if analysis_mode is AnalysisMode.REGENERATIVE:
            ConfigurationBundleService.require_change_set_ready(bundle)
        dataset_request = CanonicalDatasetRequest(
            domain=_DOMAIN[origin_module], window_start=window_start, window_end=now,
            source_labels=(origin_module,), time_anchor="module_observed_at",
            outcome_contract="systemic-module-observation-v1",
            event_identity_contract="systemic-event-identity-v1",
            filters={**filters, "entity_ids": list(entity_ids)},
        )
        dataset = CanonicalDatasetService().build(
            tenant_id=tenant_id, request=dataset_request,
            configuration_bundle_id=bundle.configuration_bundle_id,
            rows=rows, query_contract={
                "module": origin_module, "tenant_scoped": True,
                "entity_ids": list(entity_ids), "filters": filters,
            }, origin_module=origin_module,
            module_context_refs=ModuleContextRefs(), context_manifest=context_manifest,
        )
        resolution = default_registry().resolve(
            requested_provider=provider, requested_model=model,
            configured_provider=provider, configured_model=model,
            allow_request_override=False,
            required_capabilities={"text", "structured_output"},
        )
        if resolution.configured_model != resolution.effective_model:
            raise RuntimeError("CONFIGURED_EFFECTIVE_MODEL_MISMATCH")
        prompt = initial_prompt_registry().resolve("systemic-multimodule", "2.0.6")
        context = ContextFingerprint(
            profile_family=filters.get("profile_family"), timeframe=filters.get("timeframe"),
            market_regime=filters.get("market_regime"), social_regime=filters.get("social_regime"),
            risk_policy_version=filters.get("risk_policy_version"),
            strategy_exit_policy=filters.get("strategy_exit_policy"),
            feature_contract=filters.get("feature_contract"),
            label_contract=filters.get("label_contract"), model_lane=filters.get("model_lane"),
        )
        request = AIRequest(
            tenant_id=tenant_id, requested_by_user_id=user_id,
            origin_module=origin_module, origin_view=origin_view,
            analysis_mode=analysis_mode, authority=authority,
            request_intent=AIRequestIntent.NORMAL_ANALYSIS,
            provider_request=ProviderModelRequest(provider=provider, model=model),
            prompt_key=prompt.prompt_key, prompt_version=prompt.semantic_version,
            dataset_request=dataset_request, configuration_scope=ConfigurationScope(),
            question=question,
            analysis_prompt_version_id=analysis_prompt_version_id,
            frozen_context={
                "rows": rows, "context_manifest": context_manifest.model_dump(mode="json"),
                "context": context.model_dump(mode="json"), "context_fingerprint": context.digest,
                "mutation_fingerprint": mutation_fingerprint({
                    "origin_module": origin_module, "entity_ids": list(entity_ids),
                    "analysis_mode": analysis_mode.value,
                }),
                "model_approval_id": str(model_approval_id),
                "model_max_cost_usd": str(approval.max_cost_usd),
                "analysis_prompt": analysis_prompt_binding,
            },
            tool_allowlist=tuple(tool_allowlist),
            correlation_id=idempotency_key,
        )
        persistence = SQLAlchemyAIPersistence(db, tenant_id)
        for kind, value in (
            ("model_resolution", resolution), ("prompt", prompt),
            ("configuration_bundle", bundle), ("dataset", dataset), ("request", request),
        ):
            await persistence(kind, value)
        selected_graph_key = graph_key_override or cls._graph_key(analysis_mode)
        job = LeaseJob.queued(
            tenant_id=tenant_id, purpose=origin_module,
            identity={"request": str(request.ai_request_id), "graph": selected_graph_key},
        )
        await persistence("job", job)
        run = await AIGraphRunService.create(
            db, tenant_id=tenant_id, user_id=user_id,
            graph_key=selected_graph_key, ai_request_id=request.ai_request_id,
            idempotency_key=idempotency_key,
        )
        run.ai_job_id = job.id
        await db.flush()
        return run
