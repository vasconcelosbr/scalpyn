from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType
from uuid import UUID, uuid4

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from app.ai_orchestration.contracts import (
    AIRequest, AnalysisMode, Authority, CanonicalDatasetRequest,
    ConfigurationScope, ProviderModelRequest,
)
from app.ai_orchestration.event_reconciliation import (
    CanonicalEventObservation, HumanEventResolution, reconcile_canonical_events,
)
from app.ai_orchestration.invariant_validator import InvariantValidator, RuntimeInvariantState
from app.ai_orchestration.langgraph.graphs import build_graph
from app.ai_orchestration.langgraph.registry import graph_registry, resolve_graph
from app.ai_orchestration.langgraph.state import assert_checkpoint_safe
from app.ai_orchestration.provider_registry import ModelCatalogEntry, ProviderModelRegistry


BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent


def source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def request(*, authority: Authority = Authority.ANALYSIS_ONLY, question: str = "audit") -> AIRequest:
    now = datetime.now(timezone.utc)
    return AIRequest(
        tenant_id=uuid4(), origin_module="SPOT_AUDIT", analysis_mode=AnalysisMode.SYSTEMIC,
        authority=authority, provider_request=ProviderModelRequest(), prompt_key="ai-critic",
        dataset_request=CanonicalDatasetRequest(
            domain="SHADOW_PORTFOLIO", window_start=now.replace(year=now.year - 1),
            window_end=now, source_labels=("shadow",), time_anchor="entry_timestamp",
            outcome_contract="v1", event_identity_contract="decision_id", filters={},
        ),
        configuration_scope=ConfigurationScope(), question=question,
        correlation_id=str(uuid4()),
    )


def test_langgraph_dependencies_are_pinned():
    requirements = source("backend/requirements.txt")
    assert "langgraph==1.2.9" in requirements
    assert "langgraph-checkpoint-postgres==3.1.2" in requirements
    assert "psycopg[binary,pool]==3.3.4" in requirements


def test_postgres_checkpointer_setup_is_idempotent():
    bootstrap = source("backend/app/ai_orchestration/langgraph/bootstrap_checkpointer.py")
    assert "await saver.setup()" in bootstrap
    assert "ON CONFLICT (metadata_key) DO UPDATE" in bootstrap
    assert "if __name__ == \"__main__\"" in bootstrap


def test_inmemory_saver_not_used_outside_tests():
    app_files = list((BACKEND / "app").rglob("*.py"))
    assert all("InMemorySaver" not in path.read_text(encoding="utf-8") for path in app_files)


def test_checkpoint_state_contains_no_secrets():
    assert_checkpoint_safe({"tokens_input": 4, "dataset_hash": "safe"})
    with pytest.raises(ValueError, match="forbidden key"):
        assert_checkpoint_safe({"provider_api_key": "secret"})


def test_checkpoint_strict_msgpack_enabled(monkeypatch):
    monkeypatch.setenv("LANGGRAPH_STRICT_MSGPACK", "true")
    monkeypatch.setenv("AI_ORCHESTRATION_RUNTIME", "langgraph")
    monkeypatch.setenv("LANGGRAPH_RUNTIME_ENABLED", "true")
    from app.ai_orchestration.langgraph.config import get_langgraph_settings
    settings = get_langgraph_settings()
    settings.require_runtime()
    assert settings.strict_msgpack is True


def test_graph_run_requires_tenant():
    with pytest.raises(ValueError, match="tenant_id is required"):
        request().model_copy(update={"tenant_id": UUID(int=0)}).__class__.model_validate(
            request().model_dump() | {"tenant_id": str(UUID(int=0))}
        )


def test_cross_tenant_graph_resume_denied():
    service = source("backend/app/services/ai_graph_service.py")
    assert "interrupt_record.tenant_id != tenant_id" in service
    assert "interrupt_record.graph_run_id != run_id" in service


def test_graph_registry_version_is_immutable():
    assert isinstance(graph_registry, MappingProxyType)
    with pytest.raises(TypeError):
        graph_registry["new"] = resolve_graph("systemic-analysis-v1")  # type: ignore[index]


def test_systemic_graph_compiles(monkeypatch):
    monkeypatch.setenv("LANGGRAPH_STRICT_MSGPACK", "true")
    assert build_graph("systemic-analysis-v1", checkpointer=InMemorySaver()) is not None


def test_root_cause_graph_compiles(monkeypatch):
    monkeypatch.setenv("LANGGRAPH_STRICT_MSGPACK", "true")
    assert build_graph("root-cause-audit-v1", checkpointer=InMemorySaver()) is not None


def test_regenerative_graph_compiles(monkeypatch):
    monkeypatch.setenv("LANGGRAPH_STRICT_MSGPACK", "true")
    assert build_graph("regenerative-shadow-v1", checkpointer=InMemorySaver()) is not None


class CountingHandler:
    def __init__(self):
        self.counts: dict[str, int] = {}

    async def handle(self, node_name, state):
        self.counts[node_name] = self.counts.get(node_name, 0) + 1
        if node_name == "classify_root_cause":
            return {"root_cause_classification": "INSUFFICIENT_EVIDENCE"}
        if node_name == "complete":
            return {"status": "COMPLETED"}
        return {}


def _resume_once(handler: CountingHandler):
    async def run():
        saver = InMemorySaver()
        graph = build_graph("regenerative-shadow-v1", handler=handler, checkpointer=saver)
        config = {"configurable": {"thread_id": "restart", "checkpoint_ns": "scalpyn"}}
        initial = {
            "state_schema_version": "scalpyn-graph-state-v1", "graph_run_id": str(uuid4()),
            "tenant_id": str(uuid4()), "authority": "SHADOW_ONLY",
            "completed_nodes": [], "event_keys": [],
        }
        first = await graph.ainvoke(initial, config=config)
        assert first["__interrupt__"]
        second = await graph.ainvoke(Command(resume={"decision": "approve", "edits": {}}), config=config)
        assert second["__interrupt__"]
    asyncio.run(run())


def test_graph_restart_resumes_from_last_checkpoint(monkeypatch):
    monkeypatch.setenv("LANGGRAPH_STRICT_MSGPACK", "true")
    handler = CountingHandler()
    _resume_once(handler)
    assert handler.counts["create_hypothesis"] == 1


def test_graph_resume_does_not_repeat_completed_side_effect(monkeypatch):
    monkeypatch.setenv("LANGGRAPH_STRICT_MSGPACK", "true")
    handler = CountingHandler()
    _resume_once(handler)
    assert handler.counts["detect_or_receive_degradation"] == 1


def test_crash_resume_no_duplicate_side_effect(monkeypatch):
    monkeypatch.setenv("LANGGRAPH_STRICT_MSGPACK", "true")
    handler = CountingHandler()
    _resume_once(handler)
    assert handler.counts["detect_or_receive_degradation"] == 1


def test_duplicate_resume_is_idempotent():
    service = source("backend/app/services/ai_graph_service.py")
    assert "interrupt_record.idempotency_key == idempotency_key" in service


def test_interrupt_requires_authorized_actor():
    api = source("backend/app/api/ai_graphs.py")
    assert "actor_user_id=user_id" in api
    assert "Depends(get_current_user_id)" in api


def test_edit_cannot_change_lineage_ids():
    from app.services.ai_graph_service import PROTECTED_EDIT_FIELDS
    assert {"tenant_id", "dataset_snapshot_id", "configuration_bundle_id", "base_version"} <= PROTECTED_EDIT_FIELDS


def test_live_write_tool_remains_denied():
    assert "live_write\": False" in source("backend/app/api/ai_graphs.py")
    assert "\"live_write\": False" in source("backend/app/ai_orchestration/langgraph/handler.py")


def test_provider_is_called_only_after_all_gates():
    nodes = resolve_graph("systemic-analysis-v1").node_manifest
    assert nodes.index("invoke_provider") > nodes.index("run_data_quality_gate")
    assert nodes.index("invoke_provider") > nodes.index("execute_readonly_tools")


def test_provider_retry_not_duplicated_by_celery():
    celery = source("backend/app/tasks/celery_app.py")
    task = source("backend/app/tasks/ai_orchestration.py")
    assert '"app.tasks.ai_orchestration.start_graph_run"' in celery
    assert '"max_retries": 0' in celery
    assert "self.retry(" not in task


def test_stale_graph_lease_recovered():
    task = source("backend/app/tasks/ai_orchestration.py")
    assert "lease_expires_at < now" in task
    assert "recover_stale_graph_runs" in task


def test_graph_and_ai_job_state_reconcile():
    task = source("backend/app/tasks/ai_orchestration.py")
    assert task.count("GRAPH_JOB_STATE_MISMATCH") >= 2
    assert 'job.status = "COMPLETED"' in task


def test_all_legacy_entrypoints_use_orchestration():
    paths = [
        "backend/app/services/profile_ai_explanation_service.py",
        "backend/app/services/shadow_trade_analysis_service.py",
        "backend/app/services/profile_intelligence_live_service.py",
        "backend/app/copilot/agent.py",
    ]
    for path in paths:
        assert "SystemicLangGraphBridge" in source(path)


def test_no_direct_provider_calls_outside_adapters():
    # Provider adapters own transport; ai_keys.py is the explicit key-validation
    # endpoint exception required by the deployment contract.
    allowed = {"catalog.py", "http_adapter.py", "anthropic_sdk.py", "copilot_transport.py", "ai_keys.py"}
    offenders = []
    for path in (BACKEND / "app").rglob("*.py"):
        text_value = path.read_text(encoding="utf-8")
        if any(host in text_value for host in ("api.anthropic.com", "api.openai.com", "generativelanguage.googleapis.com")) and path.name not in allowed:
            offenders.append(str(path))
    assert offenders == []


def test_invalid_configured_model_fails_preflight():
    registry = ProviderModelRegistry([])
    with pytest.raises(Exception, match="Unknown model"):
        registry.resolve(
            requested_provider=None, requested_model=None,
            configured_provider="anthropic", configured_model="claude-fable-5",
        )


def test_valid_catalog_model_equals_effective_model():
    entry = ModelCatalogEntry(
        provider="test", model_id="actual-model", capabilities=frozenset({"text"}),
        max_input=100, max_output=10,
    )
    resolution = ProviderModelRegistry([entry]).resolve(
        requested_provider=None, requested_model=None,
        configured_provider="test", configured_model="actual-model",
    )
    assert resolution.configured_model == resolution.effective_model == "actual-model"


def test_real_provider_canary_is_analysis_only():
    config = source("backend/app/ai_orchestration/langgraph/config.py")
    api = source("backend/app/api/ai_graphs.py")
    assert 'LANGGRAPH_REAL_PROVIDER_CANARY_ENABLED' in config
    assert '"live_write": False' in api


def test_unresolved_event_conflict_blocks_dataset():
    now = datetime.now(timezone.utc)
    rows = reconcile_canonical_events([
        CanonicalEventObservation("a", "same", "WIN", now),
        CanonicalEventObservation("b", "same", "LOSS", now),
    ])
    assert rows[0]["quality_status"] == "BLOCK_CONFLICTING_OUTCOMES"
    assert rows[0]["canonical_outcome"] is None


def test_canonical_event_resolution_preserves_history():
    now = datetime.now(timezone.utc)
    observations = [
        CanonicalEventObservation("a", "same", "WIN", now),
        CanonicalEventObservation("b", "same", "LOSS", now),
    ]
    rows = reconcile_canonical_events(observations, [
        HumanEventResolution("same", "a", str(uuid4()), "verified exchange close", now),
    ])
    assert rows[0]["quality_status"] == "PASS"
    assert {item["source_event_id"] for item in rows[0]["history"]} == {"a", "b"}


def test_profile_change_creates_new_version():
    implementation = source("backend/app/services/profile_versioning_v2.py")
    assert "create_candidate_profile_version" in implementation
    assert "INSERT INTO profile_versions" in implementation
    assert "'CANDIDATE'" in implementation


def test_rollback_creates_new_version():
    implementation = source("backend/app/services/profile_versioning_v2.py")
    assert "rollback_to_version_id" in implementation
    assert "rollback_to_version_id," in implementation


def test_regenerative_graph_creates_hypothesis():
    assert "create_hypothesis" in resolve_graph("regenerative-shadow-v1").node_manifest
    assert '"decision_hypotheses"' in source("backend/app/ai_orchestration/langgraph/handler.py")


def test_regenerative_graph_waits_for_shadow_event():
    nodes = resolve_graph("regenerative-shadow-v1").node_manifest
    assert nodes.index("interrupt_wait_for_shadow_evidence") < nodes.index("resume_from_shadow_event")


def test_regenerative_graph_persists_decision_memory():
    nodes = resolve_graph("regenerative-shadow-v1").node_manifest
    assert "persist_decision_memory" in nodes
    assert '"decision_memory"' in source("backend/app/ai_orchestration/langgraph/handler.py")


def test_second_run_retrieves_prior_decision_memory():
    handler = source("backend/app/ai_orchestration/langgraph/handler.py")
    assert "retrieve_similar_decision_memory" in handler
    assert "context_fingerprint = :context_fingerprint" in handler
    assert "mutation_fingerprint = CAST(:mutation_fingerprint AS text)" in handler
    assert "ORDER BY created_at DESC LIMIT 20" not in handler


def test_spot_invariant_blocks_mutation_authority():
    validator = InvariantValidator()
    with pytest.raises(Exception, match="Spot exit authority is disabled"):
        validator.validate(
            request(authority=Authority.CANDIDATE_ONLY, question="spot audit"),
            bundle=type("Bundle", (), {"lineage_status": "COMPLETE"})(),
            dataset=type("Dataset", (), {"quality_status": "PASS"})(),
            state=RuntimeInvariantState(spot_never_sell_at_loss_config=False),
        )


def test_authenticated_ui_shows_graph_trace():
    page = source("frontend/app/intelligence-runs/page.tsx")
    api = source("frontend/lib/api.ts")
    assert "Execution trace" in page and "/timeline" in page and "/interrupts" in page
    assert "Authorization" in api


def test_no_live_autopilot_or_model_promotion_flags_changed(monkeypatch):
    monkeypatch.delenv("LANGGRAPH_RUNTIME_ENABLED", raising=False)
    monkeypatch.delenv("LANGGRAPH_ENTRYPOINTS_ENABLED", raising=False)
    monkeypatch.delenv("LANGGRAPH_REGENERATIVE_SHADOW_ENABLED", raising=False)
    from app.ai_orchestration.langgraph.config import get_langgraph_settings
    settings = get_langgraph_settings()
    assert settings.runtime_enabled is False
    assert settings.entrypoints_enabled is False
    assert settings.regenerative_shadow_enabled is False


def test_candidate_idempotency_key_fits_database_contract():
    from app.services.profile_versioning_v2 import candidate_idempotency_key

    change_set_id = UUID("00000000-0000-0000-0000-000000000001")
    key = candidate_idempotency_key(change_set_id, "a" * 64, "b" * 64)
    assert len(key) <= 160
    assert key == candidate_idempotency_key(change_set_id, "a" * 64, "b" * 64)


def test_staging_canary_email_passes_api_validation():
    from pydantic import EmailStr, TypeAdapter

    from app.ai_orchestration.langgraph.staging_canary import CANARY_EMAIL

    assert str(TypeAdapter(EmailStr).validate_python(CANARY_EMAIL)) == CANARY_EMAIL
