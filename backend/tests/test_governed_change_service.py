from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import importlib.util
import inspect
import json
from pathlib import Path
from types import SimpleNamespace
import uuid

import pytest

from app.models.profile_audit_log import ProfileAuditLog
from app.models.copilot import CopilotAuditLog
from app.models.ai_graph import AIGraphEvent
from app.services import governed_change_service as service
from app.services.governed_change_service import (
    ALLOWED_CONFIG_TYPES,
    GovernedChangePathError,
    PROFILE_ROOTS,
    apply_typed_patch,
)


class _ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value

    def scalars(self):
        return self

    def all(self):
        if self.value is None:
            return []
        return self.value if isinstance(self.value, list) else [self.value]


class _FakeDB:
    def __init__(self, result):
        self.result = result
        self.added = []
        self.commits = 0
        self.flushes = 0

    async def execute(self, _query):
        return _ScalarResult(self.result)

    def add(self, value):
        self.added.append(value)

    async def commit(self):
        self.commits += 1

    async def flush(self):
        self.flushes += 1

    async def refresh(self, _value):
        return None


def test_profile_patch_returns_an_auditable_before_after_diff():
    source = {
        "default_timeframe": "5m",
        "filters": {"logic": "AND", "conditions": []},
        "scoring": {"enabled": True, "selected_rule_ids": ["r1"]},
        "signals": {"logic": "AND", "conditions": []},
        "block_rules": {"blocks": []},
        "entry_triggers": {"logic": "AND", "conditions": []},
    }
    candidate, diff = apply_typed_patch(source, [{
        "op": "replace",
        "path": "/default_timeframe",
        "old_value": "5m",
        "value": "15m",
        "array_guards": [],
        "reason": "Use the evidenced decision horizon",
        "evidence_refs": ["evidence-1"],
    }], allowed_roots=PROFILE_ROOTS)

    assert source["default_timeframe"] == "5m"
    assert candidate["default_timeframe"] == "15m"
    assert diff == [{
        "op": "replace",
        "path": "/default_timeframe",
        "old_value": "5m",
        "value": "15m",
        "reason": "Use the evidenced decision horizon",
        "evidence_refs": ["evidence-1"],
    }]


def test_patch_rejects_sensitive_or_unknown_configuration_paths():
    with pytest.raises(GovernedChangePathError, match="Sensitive field"):
        apply_typed_patch(
            {"provider": {}},
            [{"op": "add", "path": "/provider/api_key", "value": "x"}],
        )
    with pytest.raises(GovernedChangePathError, match="Unknown configuration root"):
        apply_typed_patch(
            {"thresholds": {}},
            [{"op": "add", "path": "/new_runtime_gate", "value": True}],
        )


def test_profile_patch_fails_closed_on_named_key_inside_an_array():
    source = {"scoring": {"rules": []}}

    with pytest.raises(GovernedChangePathError, match="Expected an object"):
        apply_typed_patch(source, [{
            "op": "replace",
            "path": "/scoring/rules/rsi_overbought_penalty",
            "old_value": None,
            "value": 2,
            "array_guards": [],
        }], allowed_roots=PROFILE_ROOTS)


def test_array_patch_requires_unique_stable_identity_at_the_proposed_index():
    source = {
        "filters": {
            "conditions": [
                {"field": "adx", "value": 18},
                {"field": "rsi", "value": 68},
            ]
        }
    }
    change = [{
        "op": "replace",
        "path": "/filters/conditions/0/value",
        "old_value": 18,
        "value": 22,
        "array_guards": [{
            "path": "/filters/conditions/0",
            "identity": {"field": "adx"},
        }],
    }]

    candidate, _ = apply_typed_patch(source, change, allowed_roots=PROFILE_ROOTS)
    assert candidate["filters"]["conditions"][0]["value"] == 22

    reordered = {
        "filters": {
            "conditions": [
                {"field": "rsi", "value": 68},
                {"field": "adx", "value": 18},
            ]
        }
    }
    with pytest.raises(GovernedChangePathError, match="another index"):
        apply_typed_patch(reordered, change, allowed_roots=PROFILE_ROOTS)


def test_array_patch_rejects_missing_guard_and_stale_old_value():
    source = {"scoring_rules": [{"id": "rule-rsi", "points": 4}]}

    with pytest.raises(GovernedChangePathError, match="identity guard"):
        apply_typed_patch(source, [{
            "op": "replace",
            "path": "/scoring_rules/0/points",
            "old_value": 4,
            "value": 2,
            "array_guards": [],
        }])
    with pytest.raises(GovernedChangePathError, match="Stale old_value"):
        apply_typed_patch(source, [{
            "op": "replace",
            "path": "/scoring_rules/0/points",
            "old_value": 3,
            "value": 2,
            "array_guards": [{
                "path": "/scoring_rules/0",
                "identity": {"id": "rule-rsi"},
            }],
        }])


def test_array_patch_rejects_replacing_an_element_with_a_new_identity():
    source = {"filters": {"conditions": [{"field": "adx", "value": 18}]}}

    with pytest.raises(GovernedChangePathError, match="identity cannot change"):
        apply_typed_patch(source, [{
            "op": "replace",
            "path": "/filters/conditions/0",
            "old_value": {"field": "adx", "value": 18},
            "value": {"field": "rsi", "value": 18},
            "array_guards": [{
                "path": "/filters/conditions/0",
                "identity": {"field": "adx"},
            }],
        }], allowed_roots=PROFILE_ROOTS)


def test_patch_rejects_an_exact_replacement_noop():
    with pytest.raises(GovernedChangePathError, match="no-op"):
        apply_typed_patch({"thresholds": {"buy": 65}}, [{
            "op": "replace",
            "path": "/thresholds/buy",
            "old_value": 65,
            "value": 65,
            "array_guards": [],
        }])


def test_profile_normalization_rejects_discarded_or_noop_patch():
    source = {
        "default_timeframe": "5m",
        "filters": {"logic": "AND", "conditions": []},
        "scoring": {},
        "signals": {},
        "block_rules": {},
        "entry_triggers": {},
    }
    changes = [{
        "op": "add",
        "path": "/filters/unsupported_contract_key",
        "old_value": None,
        "value": 123,
        "array_guards": [],
    }]
    patched, _ = apply_typed_patch(source, changes, allowed_roots=PROFILE_ROOTS)
    normalized_before = service._validate_profile_config(source)
    normalized_candidate = service._validate_profile_config(patched)

    with pytest.raises(GovernedChangePathError, match="no-op"):
        service._assert_patch_survived_normalization(
            normalized_before,
            normalized_candidate,
            changes,
        )


def test_noncanonical_profile_source_cannot_hide_unreviewed_normalization():
    legacy = {
        "filters": {
            "conditions": [
                {"indicator": "adx", "operator": ">=", "value": 20}
            ]
        }
    }
    normalized = {
        "default_timeframe": "5m",
        "filters": {
            "logic": "AND",
            "conditions": [
                {"field": "adx", "operator": ">=", "value": 20}
            ],
        },
    }

    with pytest.raises(
        GovernedChangePathError,
        match="separate canonicalization preview",
    ):
        service._require_canonical_profile_source(legacy, normalized)


def test_inapplicable_governed_proposal_uses_a_stable_terminal_code():
    from app.ai_orchestration.errors import GovernedProposalError, GraphNodeExecutionError
    from app.ai_orchestration.langgraph.analysis_chat_handler import (
        AnalysisChatGraphNodeHandler,
    )
    from app.tasks.ai_orchestration import _failure_details

    handler_source = inspect.getsource(AnalysisChatGraphNodeHandler._node_updates)
    assert (
        "except (AttributeError, KeyError, LookupError, TypeError, ValueError) as exc"
        in handler_source
    )
    assert "raise GovernedProposalError() from exc" in handler_source

    failure = _failure_details(GraphNodeExecutionError(
        "draft_proposal_if_confirmed",
        GovernedProposalError(),
    ))
    assert failure == {
        "failed_node": "draft_proposal_if_confirmed",
        "error_kind": "GOVERNED_PROPOSAL_INVALID",
        "reason_code": "ANALYSIS_CHAT_PROPOSAL_NOT_APPLICABLE",
        "safe_message": (
            "A governed preview could not be generated because the proposed paths "
            "do not match the current configuration contract"
        ),
        "provider_transport_attempted": True,
        "terminal_reason": "FAIL_CLOSED",
        "diagnostics": None,
    }


def test_concrete_proposal_prompt_rejects_pseudo_paths_and_can_return_limitation():
    from app.ai_orchestration.provider_adapters.http_adapter import (
        anthropic_output_config,
    )

    migration_path = (
        Path(__file__).resolve().parents[1]
        / "alembic/versions/167_chat_concrete_proposal_paths.py"
    )
    spec = importlib.util.spec_from_file_location("concrete_paths_migration", migration_path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    prompt = migration._prompt_content()
    schema = prompt["output_schema_json"]
    system = prompt["system_template"]
    proposal_options = schema["properties"]["proposal"]["anyOf"]

    assert migration.revision == "167_chat_concrete_paths"
    assert migration.down_revision == "166_chat_compact_proposals"
    assert prompt["semantic_version"] == "1.5.0"
    target_config_types = (
        proposal_options[0]["properties"]["target"]["properties"]["config_type"]["enum"]
    )
    assert target_config_types == ["score", None]
    assert "Spot, futures, risk, strategy" in system
    assert schema["properties"]["answer_type"]["enum"] == ["PROPOSAL", "LIMITATION"]
    assert proposal_options[1] == {"type": "null"}
    assert "MUST use the actual zero-based decimal index" in system
    assert "target.config_type=score" in system
    assert "currently supports only the complete global score" in system
    assert "Spot, futures, risk, strategy and every other config" in system
    assert "/scoring_rules/17/points" not in system
    assert "Never copy a fixed example index" in system
    assert "old_value_json" in schema["properties"]["proposal"]["anyOf"][0]["properties"]["changes"]["items"]["required"]
    assert "array_guards_json" in schema["properties"]["proposal"]["anyOf"][0]["properties"]["changes"]["items"]["required"]
    assert "/scoring/rules/rsi_overbought_penalty" in system
    assert "proposal=null" in system

    prepared = anthropic_output_config(schema)["format"]["schema"]
    prepared_proposal = prepared["properties"]["proposal"]["anyOf"][0]
    prepared_change = prepared_proposal["properties"]["changes"]["items"]
    assert prepared_proposal["properties"]["target"]["properties"]["config_type"]["enum"] == [
        "score", None,
    ]
    prepared_config_type = prepared_proposal["properties"]["target"]["properties"][
        "config_type"
    ]
    assert prepared_config_type["anyOf"] == [
        {"type": "string"},
        {"type": "null"},
    ]
    assert "type" not in prepared_config_type
    assert prepared_proposal["additionalProperties"] is False
    assert prepared_proposal["properties"]["target"]["additionalProperties"] is False
    assert prepared_change["additionalProperties"] is False
    assert "maxItems" not in prepared_proposal["properties"]["changes"]

    migration_source = inspect.getsource(migration)
    downgrade_source = inspect.getsource(migration.downgrade)
    assert "ANALYSIS_CHAT_PROMPT_1_5_CONFLICT" in migration_source
    assert "ai_prompt_versions.content_hash = EXCLUDED.content_hash" in migration_source
    assert "status = 'DEPRECATED'" in downgrade_source
    assert "DELETE FROM ai_prompt_versions" not in downgrade_source


def test_original_proposal_schema_is_revalidated_after_anthropic_preparation():
    from app.ai_orchestration.errors import ProviderOutputError
    from app.ai_orchestration.langgraph.analysis_chat_handler import (
        _normalized_provider_mode,
        _validated_provider_answer,
    )

    migration_path = (
        Path(__file__).resolve().parents[1]
        / "alembic/versions/167_chat_concrete_proposal_paths.py"
    )
    spec = importlib.util.spec_from_file_location("validated_schema_migration", migration_path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    schema = migration._prompt_content()["output_schema_json"]
    parent_id = uuid.uuid4()
    evidence_id = uuid.uuid4()
    limitation = {
        "answer": "Não há caminho concreto comprovado.",
        "answer_type": "LIMITATION",
        "based_on": "PROPOSAL_DRAFT",
        "parent_analysis_run_id": str(parent_id),
        "evidence_refs": [{"evidence_id": str(evidence_id)}],
        "proposal": None,
    }

    answer = _validated_provider_answer(limitation, schema)
    assert _normalized_provider_mode(
        answer,
        is_proposal=True,
        refreshed=False,
    ) == ("LIMITATION", "PROPOSAL_DRAFT", None)

    invalid_format = {
        **limitation,
        "evidence_refs": [{"evidence_id": "not-a-uuid"}],
    }
    with pytest.raises(ProviderOutputError) as exc_info:
        _validated_provider_answer(invalid_format, schema)
    assert exc_info.value.reason_code == "ANALYSIS_CHAT_OUTPUT_SCHEMA_INVALID"

    change = {
        "op": "replace",
        "path": "/default_timeframe",
        "value_json": '"15m"',
        "old_value_json": '"5m"',
        "array_guards_json": "[]",
        "reason": "evidence",
        "evidence_refs": [str(evidence_id)],
        "profile_id": str(uuid.uuid4()),
        "profile_name": "Profile A",
        "profile_indexes": [],
    }
    too_many_changes = {
        **limitation,
        "answer_type": "PROPOSAL",
        "proposal": {
            "operation_type": "UPDATE_PROFILE_CONFIG",
            "target": {
                "profile_id": change["profile_id"],
                "profile_name": "Profile A",
                "config_type": None,
                "pool_id": None,
                "profile_ids": [],
            },
            "objective": "Update profile",
            "risk": "Operational change",
            "changes": [change] * 65,
        },
    }
    with pytest.raises(ProviderOutputError) as exc_info:
        _validated_provider_answer(too_many_changes, schema)
    assert exc_info.value.reason_code == "ANALYSIS_CHAT_OUTPUT_SCHEMA_INVALID"


def test_governed_staging_canary_fixture_passes_original_prompt_1_5_schema():
    from app.ai_orchestration.langgraph.analysis_chat_handler import (
        GOVERNED_STAGING_CANARY_CANDIDATE_VALUE,
        GOVERNED_STAGING_CANARY_SOURCE_VALUE,
        _governed_staging_canary_raw_proposal,
        _validated_provider_answer,
    )

    migration_path = (
        Path(__file__).resolve().parents[1]
        / "alembic/versions/167_chat_concrete_proposal_paths.py"
    )
    spec = importlib.util.spec_from_file_location("canary_schema_migration", migration_path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    profile_id = uuid.uuid4()
    evidence_id = uuid.uuid4()
    parent_id = uuid.uuid4()
    proposal = _governed_staging_canary_raw_proposal(
        profile_id=profile_id,
        evidence_id=evidence_id,
    )

    validated = _validated_provider_answer(
        {
            "answer": "Deterministic staging-only governed preview.",
            "answer_type": "PROPOSAL",
            "based_on": "PROPOSAL_DRAFT",
            "parent_analysis_run_id": str(parent_id),
            "evidence_refs": [{"evidence_id": str(evidence_id)}],
            "proposal": proposal,
        },
        migration._prompt_content()["output_schema_json"],
    )

    assert validated.answer_type == "PROPOSAL"
    assert proposal["operation_type"] == "UPDATE_PROFILE_CONFIG"
    assert proposal["target"]["profile_id"] == str(profile_id)
    assert proposal["changes"] == [{
        "op": "replace",
        "path": "/filters/conditions/0/value",
        "value_json": json.dumps(
            GOVERNED_STAGING_CANARY_CANDIDATE_VALUE,
            separators=(",", ":"),
        ),
        "old_value_json": json.dumps(
            GOVERNED_STAGING_CANARY_SOURCE_VALUE,
            separators=(",", ":"),
        ),
        "array_guards_json": json.dumps([{
            "path": "/filters/conditions/0",
            "identity": {"field": "taker_ratio"},
        }], separators=(",", ":")),
        "reason": "Deterministic staging proof of a monotonic eligibility restriction",
        "evidence_refs": [str(evidence_id)],
        "profile_id": None,
        "profile_name": None,
        "profile_indexes": [],
    }]


def test_governed_staging_canary_candidate_passes_exact_policy_fixture():
    from app.ai_orchestration.langgraph.analysis_chat_handler import (
        GOVERNED_STAGING_CANARY_CANDIDATE_VALUE,
        GOVERNED_STAGING_CANARY_SOURCE_VALUE,
        governed_staging_canary_profile_config,
    )
    from app.ai_orchestration.langgraph.staging_canary import (
        _canary_risk_policy,
        _canary_score_policy,
        _canary_strategy_policy,
    )

    plan, policies, profiles = _candidate_validation_fixture()
    source = governed_staging_canary_profile_config(
        value=GOVERNED_STAGING_CANARY_SOURCE_VALUE
    )
    candidate = governed_staging_canary_profile_config(
        value=GOVERNED_STAGING_CANARY_CANDIDATE_VALUE
    )
    plan.execution_payload["source_document"] = source
    plan.execution_payload["candidate_document"] = candidate
    plan.proposed_diff = [{
        "op": "replace",
        "path": "/filters/conditions/0/value",
        "old_value": GOVERNED_STAGING_CANARY_SOURCE_VALUE,
        "value": GOVERNED_STAGING_CANARY_CANDIDATE_VALUE,
        "reason": "deterministic staging eligibility restriction",
        "evidence_refs": [str(uuid.uuid4())],
    }]
    profiles[0].config = source
    profiles[0].is_active = False
    next(item for item in policies if item.config_type == "risk").config_json = (
        _canary_risk_policy()
    )
    next(item for item in policies if item.config_type == "strategy").config_json = (
        _canary_strategy_policy()
    )
    next(item for item in policies if item.config_type == "score").config_json = (
        _canary_score_policy()
    )

    result = service._candidate_validation_result(plan, policies, profiles)

    assert result["decision"] == "PASS"
    assert result["policy_semantic_validation"] == {
        "risk": "PASS",
        "strategy": "PASS",
    }
    assert result["terminal_reason"] == "POLICY_SEMANTIC_GUARDS_PASS"


def test_profile_cache_result_is_explicitly_not_applicable():
    assert service._profile_cache_not_required() == {
        "cache_invalidation_status": "NOT_REQUIRED",
        "cache_reconciliation_retry_state": "NOT_APPLICABLE",
        "cache_reconciliation_attempts": 0,
        "cache_reconciliation_max_attempts": 0,
        "cache_reconciliation_next_retry_at": None,
        "cache_reconciliation_dispatch_lease_until": None,
    }


def test_proposal_materialization_decodes_guards_and_fails_closed_on_bad_json():
    from app.ai_orchestration.langgraph.analysis_chat_handler import (
        _materialize_governed_proposal,
    )

    evidence_id = str(uuid.uuid4())
    raw = {
        "operation_type": "UPDATE_PROFILE_CONFIG",
        "target": {"profile_id": str(uuid.uuid4())},
        "objective": "Update profile",
        "risk": "Operational change",
        "changes": [{
            "op": "replace",
            "path": "/filters/conditions/0/value",
            "value_json": "22",
            "old_value_json": "18",
            "array_guards_json": (
                '[{"path":"/filters/conditions/0","identity":{"field":"adx"}}]'
            ),
            "reason": "evidence",
            "evidence_refs": [evidence_id],
            "profile_id": None,
            "profile_name": None,
            "profile_indexes": [],
        }],
    }

    materialized = _materialize_governed_proposal(raw, {evidence_id})
    assert materialized["changes"][0]["old_value"] == 18
    assert materialized["changes"][0]["value"] == 22
    assert materialized["changes"][0]["array_guards"] == [{
        "path": "/filters/conditions/0",
        "identity": {"field": "adx"},
    }]

    invalid = {
        **raw,
        "changes": [{**raw["changes"][0], "array_guards_json": "{"}],
    }
    with pytest.raises(ValueError, match="Invalid governed change JSON contract"):
        _materialize_governed_proposal(invalid, {evidence_id})


def test_chat_config_authority_excludes_self_modifying_and_secret_families():
    assert ALLOWED_CONFIG_TYPES == {"score", "spot_engine", "futures_engine"}
    assert "risk" not in ALLOWED_CONFIG_TYPES
    assert "strategy" not in ALLOWED_CONFIG_TYPES
    assert "ai_analysis_chat_runtime" not in ALLOWED_CONFIG_TYPES
    assert "ai_provider_runtime" not in ALLOWED_CONFIG_TYPES
    assert "ml" not in ALLOWED_CONFIG_TYPES


def test_bulk_profile_patch_keeps_each_profile_diff_separate():
    first = {"scoring": {"weights": {"rsi": 4}}}
    second = {"scoring": {"weights": {"rsi": 3}}}
    first_candidate, first_diff = apply_typed_patch(first, [{
        "op": "replace", "path": "/scoring/weights/rsi", "value": 2,
        "old_value": 4, "array_guards": [],
        "reason": "evidence", "evidence_refs": ["e1"],
    }], allowed_roots=PROFILE_ROOTS)
    second_candidate, second_diff = apply_typed_patch(second, [{
        "op": "replace", "path": "/scoring/weights/rsi", "value": 2,
        "old_value": 3, "array_guards": [],
        "reason": "evidence", "evidence_refs": ["e2"],
    }], allowed_roots=PROFILE_ROOTS)

    assert first_candidate["scoring"]["weights"]["rsi"] == 2
    assert second_candidate["scoring"]["weights"]["rsi"] == 2
    assert first_diff[0]["old_value"] == 4
    assert second_diff[0]["old_value"] == 3


def _score_document(*, points=25):
    return {
        "weights": {
            "liquidity": 25,
            "market_structure": 25,
            "momentum": 25,
            "signal": 25,
        },
        "scoring_rules": [{
            "id": "rule-adx",
            "indicator": "adx",
            "operator": ">=",
            "value": 25,
            "points": points,
            "category": "momentum",
        }],
        "thresholds": {"strong_buy": 80, "buy": 65, "neutral": 40},
        "auto_select_top_n": 5,
        "auto_select_min_score": 80,
    }


def _candidate_validation_fixture(*, spot_never_sell_at_loss=True, selected_rule_ids=None):
    selected = ["rule-adx"] if selected_rule_ids is None else selected_rule_ids
    source = service._validate_profile_config({
        "default_timeframe": "5m",
        "scoring": {"enabled": True, "selected_rule_ids": selected},
    })
    candidate = {**source, "default_timeframe": "15m"}
    plan = SimpleNamespace(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        action_type=service.ACTION_TYPE,
        target_type="PROFILE",
        target_id=None,
        objective="Use the evidenced timeframe",
        risk_assessment="Operational configuration change",
        target_state_hash="source-state-hash",
        rollback_plan={"action": "RESTORE_SNAPSHOT"},
        status="DRY_RUN",
        evidence={},
        proposed_diff=[{
            "op": "replace",
            "path": "/default_timeframe",
            "old_value": "5m",
            "value": "15m",
            "reason": "evidence",
            "evidence_refs": [str(uuid.uuid4())],
        }],
        execution_payload={
            "operation_type": "UPDATE_PROFILE_CONFIG",
            "profile_id": str(uuid.uuid4()),
            "source_document": source,
            "candidate_document": candidate,
        },
    )
    now = datetime(2026, 8, 13, tzinfo=timezone.utc)

    def policy(config_type, config_json):
        return SimpleNamespace(
            id=uuid.uuid4(),
            user_id=plan.user_id,
            pool_id=None,
            config_type=config_type,
            config_json=config_json,
            is_active=True,
            updated_at=now,
        )

    policies = [
        policy("risk", {"enabled": True}),
        policy("strategy", {"execution": {"enabled": True}}),
        policy("spot_engine", {
            "selling": {"never_sell_at_loss": spot_never_sell_at_loss},
        }),
        policy("score", _score_document()),
    ]
    profile = SimpleNamespace(
        id=uuid.UUID(plan.execution_payload["profile_id"]),
        name="Profile A",
        profile_role="acquisition_queue",
        pipeline_label="L3_PROFILE_A",
        generated_by=None,
        config=source,
        is_active=True,
        profile_version=now,
        updated_at=now,
    )
    plan.target_id = str(profile.id)
    return plan, policies, [profile]


def _install_executable_policy_semantics(policies):
    next(item for item in policies if item.config_type == "risk").config_json = {
        "take_profit_pct": 1.5,
        "stop_loss_atr_multiplier": 1.5,
        "trailing_stop_enabled": False,
        "max_positions": 5,
        "daily_loss_limit_pct": 3.0,
        "max_exposure_per_asset_pct": 20,
        "circuit_breaker_consecutive_losses": 3,
        "circuit_breaker_pause_minutes": 60,
        "default_order_type": "limit",
        "max_slippage_pct": 0.1,
        "capital_per_trade_pct": 10,
        "max_capital_in_use_pct": 80,
    }
    next(item for item in policies if item.config_type == "strategy").config_json = {
        "strategies": [
            {
                "id": "momentum_breakout",
                "name": "Momentum Breakout",
                "enabled": True,
                "params": {
                    "volume_spike_multiplier": 2,
                    "adx_min": 25,
                    "lookback": 20,
                },
            },
            {
                "id": "mean_reversion",
                "name": "Mean Reversion",
                "enabled": False,
                "params": {
                    "rsi_threshold": 30,
                    "bollinger_deviation": 2.0,
                    "zscore_threshold": -2.0,
                },
            },
        ],
    }


def _set_profile_condition_change(plan, profile, *, field, old_value, new_value):
    source = deepcopy(plan.execution_payload["source_document"])
    source["filters"]["conditions"] = [{
        "field": field,
        "operator": ">=",
        "value": old_value,
    }]
    candidate = deepcopy(source)
    candidate["filters"]["conditions"][0]["value"] = new_value
    plan.execution_payload["source_document"] = source
    plan.execution_payload["candidate_document"] = candidate
    plan.proposed_diff = [{
        "op": "replace",
        "path": "/filters/conditions/0/value",
        "old_value": old_value,
        "value": new_value,
        "reason": "policy test",
        "evidence_refs": [str(uuid.uuid4())],
    }]
    profile.config = source


def test_candidate_aware_validator_blocks_profile_diff_without_policy_semantics():
    plan, policies, profiles = _candidate_validation_fixture()

    result = service._candidate_validation_result(plan, policies, profiles)

    assert result["decision"] == "VETO"
    assert result["terminal_reason"] == "GLOBAL_RISK_VETO"
    assert result["risk_validation"] == "VETO"
    assert result["strategy_validation"] == "VETO"
    assert result["deterministic_guard_validation"] == {
        "risk": "VETO",
        "strategy": "INVARIANT_CONFLICT",
    }
    assert {item["family"] for item in result["policy_snapshots"]} == {
        "risk", "strategy", "score",
    }
    assert [item["id"] for item in result["profile_dependency_snapshots"]] == [
        str(profiles[0].id)
    ]
    assert result["profile_dependency_snapshot_hash"] == service.document_hash(
        result["profile_dependency_snapshots"]
    )
    assert result["plan_binding_hash"] == service.plan_binding_hash(plan)
    assert result["validation_scope"] == "CANDIDATE_SCHEMA_AND_PERSISTED_POLICY_SEMANTICS"
    assert result["policy_semantic_validation"] == {"risk": "VETO", "strategy": "VETO"}
    assert not any(item["decision"] == "NOT_APPLICABLE" for item in result["checks"])
    assert result["not_validated"] == [
        "any risk/strategy semantics marked NOT_PERFORMED in policy_semantic_validation",
        "provider or model judgment",
        "profitability, backtest or shadow outcome",
        "exchange state, order placement or live execution",
        "numeric limits absent from registered schemas or persisted policy JSON",
    ]


def test_unrelated_spot_drift_is_not_mislabeled_as_risk_policy_approval():
    plan, policies, profiles = _candidate_validation_fixture(spot_never_sell_at_loss=False)

    result = service._candidate_validation_result(plan, policies, profiles)

    assert result["decision"] == "VETO"
    assert result["risk_validation"] == "VETO"
    assert result["strategy_validation"] == "VETO"
    assert "spot" not in {item["family"] for item in result["policy_snapshots"]}


def test_execution_fence_rejects_false_policy_pass_labels_for_unperformed_scope():
    plan, policies, profiles = _candidate_validation_fixture()
    validation = service._candidate_validation_result(plan, policies, profiles)
    forged = deepcopy(validation)
    forged["risk_validation"] = "PASS"
    forged["strategy_validation"] = "PASS"
    plan.evidence["candidate_validation"] = forged

    with pytest.raises(
        service.GovernedExecutionFenceError,
        match="ANALYSIS_CHAT_CANDIDATE_VALIDATION_REQUIRED",
    ):
        service._require_persisted_candidate_pass(plan, validation)


def test_execution_fence_rejects_forged_unperformed_scope_for_engine_mutation():
    plan, policies, profiles = _candidate_validation_fixture()
    forged = service._candidate_validation_result(plan, policies, profiles)
    plan.execution_payload["operation_type"] = "UPDATE_CONFIG_PROFILE"
    plan.execution_payload["config_type"] = "futures_engine"
    plan.evidence["candidate_validation"] = forged

    with pytest.raises(
        service.GovernedExecutionFenceError,
        match="ANALYSIS_CHAT_CANDIDATE_VALIDATION_REQUIRED",
    ):
        service._require_persisted_candidate_pass(plan, forged)


def test_unregistered_policy_semantics_fail_closed_without_false_pass():
    plan, policies, profiles = _candidate_validation_fixture()
    source = service.FuturesEngineConfig().model_dump()
    candidate = deepcopy(source)
    candidate["risk"]["max_positions"] = source["risk"]["max_positions"] - 1
    futures = SimpleNamespace(
        id=uuid.uuid4(),
        user_id=plan.user_id,
        pool_id=None,
        config_type="futures_engine",
        config_json=source,
        is_active=True,
        updated_at=datetime(2026, 8, 13, tzinfo=timezone.utc),
    )
    policies.append(futures)
    plan.target_type = "CONFIG_PROFILE"
    plan.target_id = str(futures.id)
    plan.execution_payload = {
        "operation_type": "UPDATE_CONFIG_PROFILE",
        "config_profile_id": str(futures.id),
        "config_type": "futures_engine",
        "pool_id": None,
        "source_document": source,
        "candidate_document": candidate,
    }
    plan.proposed_diff = [{
        "op": "replace",
        "path": "/risk/max_positions",
        "old_value": source["risk"]["max_positions"],
        "value": candidate["risk"]["max_positions"],
        "reason": "evidence",
        "evidence_refs": [str(uuid.uuid4())],
    }]

    result = service._candidate_validation_result(plan, policies, profiles)

    assert result["decision"] == "VETO"
    assert result["risk_validation"] == "NOT_PERFORMED"
    assert result["strategy_validation"] == "NOT_PERFORMED"
    assert result["policy_semantic_validation"] == {
        "risk": "NOT_PERFORMED",
        "strategy": "NOT_PERFORMED",
    }
    assert not (
        result["risk_validation"] == "PASS"
        or result["strategy_validation"] == "PASS"
    )


def test_spot_risk_semantics_can_pass_but_missing_strategy_mapping_still_vetoes():
    plan, policies, _profiles = _candidate_validation_fixture()
    risk_record = next(item for item in policies if item.config_type == "risk")
    risk_record.config_json = {
        "capital_per_trade_pct": 10,
        "max_capital_in_use_pct": 80,
        "max_exposure_per_asset_pct": 20,
        "max_positions": 10,
        "max_slippage_pct": 0.1,
        "default_order_type": "limit",
    }
    source = service.SpotEngineConfig().model_dump()
    source["selling"]["never_sell_at_loss"] = True
    source["buying"].update({
        "capital_per_trade_pct": 10,
        "max_capital_in_use_pct": 80,
        "max_exposure_per_asset_pct": 20,
        "max_positions_total": 10,
        "order_type": "limit",
    })
    candidate = deepcopy(source)
    candidate["buying"]["capital_per_trade_pct"] = 9
    spot = next(item for item in policies if item.config_type == "spot_engine")
    spot.config_json = source
    plan.target_type = "CONFIG_PROFILE"
    plan.target_id = str(spot.id)
    plan.execution_payload = {
        "operation_type": "UPDATE_CONFIG_PROFILE",
        "config_profile_id": str(spot.id),
        "config_type": "spot_engine",
        "pool_id": None,
        "source_document": source,
        "candidate_document": candidate,
    }
    plan.proposed_diff = [{
        "op": "replace",
        "path": "/buying/capital_per_trade_pct",
        "old_value": 10,
        "value": 9,
        "reason": "evidence",
        "evidence_refs": [str(uuid.uuid4())],
    }]

    result = service._candidate_validation_result(plan, policies, [])

    # Risk really was evaluated; the write still cannot proceed because the
    # persisted strategy catalog exposes no executable Spot mapping.
    assert result["risk_validation"] == "PASS"
    assert result["strategy_validation"] == "NOT_PERFORMED"
    assert result["decision"] == "VETO"
    assert result["terminal_reason"] == "STRATEGY_INVARIANT_CONFLICT"


def test_unrelated_structural_profile_change_fails_without_runtime_scope_proof():
    plan, policies, profiles = _candidate_validation_fixture()
    _install_executable_policy_semantics(policies)
    profiles[0].name = "Momentum Breakout"

    result = service._candidate_validation_result(plan, policies, profiles)

    assert result["decision"] == "VETO"
    assert result["risk_validation"] == "PASS"
    assert result["strategy_validation"] == "VETO"
    assert result["policy_semantic_validator_version"] == "profile-score-policy-v1"
    risk_evidence = result["policy_semantic_evidence"]["risk"]
    assert risk_evidence["authority_scope"] == "PROFILE_SCORE_SIGNAL_ONLY"
    assert risk_evidence["downstream_caps_hash"] == service.document_hash(
        risk_evidence["downstream_caps"]
    )
    assert risk_evidence["downstream_caps"]["circuit_breaker_pause_minutes"] == 60
    strategy_evidence = result["policy_semantic_evidence"]["strategy"]
    assert strategy_evidence["validation_mode"] == (
        "CONSERVATIVE_GLOBAL_FLOOR_COMPATIBILITY"
    )
    assert "no proven hard-floor semantic mapping" in strategy_evidence["basis"]
    lookback = next(
        item for item in strategy_evidence["scoped_out_constraints"]
        if item["parameter"] == "lookback"
    )
    assert lookback["scope_reason"] == (
        "LOOKBACK_HAS_NO_PROVEN_PROFILE_SCORE_RUNTIME_MAPPING"
    )


def test_additive_score_rule_change_cannot_claim_a_hard_strategy_floor():
    plan, policies, profiles = _candidate_validation_fixture()
    _install_executable_policy_semantics(policies)
    profiles[0].name = "Momentum Breakout"
    score = next(item for item in policies if item.config_type == "score")
    source = deepcopy(score.config_json)
    candidate = deepcopy(source)
    candidate["scoring_rules"][0]["points"] = 20
    plan.target_type = "CONFIG_PROFILE"
    plan.target_id = str(score.id)
    plan.execution_payload = {
        "operation_type": "UPDATE_CONFIG_PROFILE",
        "config_profile_id": str(score.id),
        "config_type": "score",
        "pool_id": None,
        "source_document": source,
        "candidate_document": candidate,
    }
    plan.proposed_diff = [{
        "op": "replace",
        "path": "/scoring_rules/0/points",
        "old_value": 25,
        "value": 20,
        "reason": "policy test",
        "evidence_refs": [str(uuid.uuid4())],
    }]

    result = service._candidate_validation_result(plan, policies, profiles)

    assert result["decision"] == "VETO"
    assert result["risk_validation"] == "PASS"
    assert result["strategy_validation"] == "VETO"
    comparison = next(
        item for item in result["policy_semantic_evidence"]["strategy"]["comparisons"]
        if item["concept"] == "adx"
    )
    assert comparison["effective_global_minimum"] == 25
    assert comparison["candidate_assertion"]["value"] == 25
    assert comparison["decision"] == "VETO"
    assert "ADDITIVE_SCORING" in comparison["reason"]


@pytest.mark.parametrize(
    ("field", "old_value", "new_value", "expected"),
    [
        ("adx", 30, 20, "VETO"),
        ("adx", 20, 30, "PASS"),
        ("volume_spike", 2.5, 1.5, "VETO"),
        ("volume_spike", 1.5, 2.5, "PASS"),
    ],
)
def test_active_strategy_thresholds_are_compared_to_profile_candidate(
    field,
    old_value,
    new_value,
    expected,
):
    plan, policies, profiles = _candidate_validation_fixture()
    _install_executable_policy_semantics(policies)
    _set_profile_condition_change(
        plan,
        profiles[0],
        field=field,
        old_value=old_value,
        new_value=new_value,
    )

    result = service._candidate_validation_result(plan, policies, profiles)

    assert result["risk_validation"] == "PASS"
    assert result["strategy_validation"] == expected
    assert result["decision"] == expected
    comparisons = result["policy_semantic_evidence"]["strategy"]["comparisons"]
    comparison = next(item for item in comparisons if item["concept"] == field)
    assert comparison["effective_global_minimum"] == (25 if field == "adx" else 2)
    assert comparison["decision"] == expected
    assert comparison["candidate_assertion"]["required_basis"] == (
        "FILTER_AND_MEMBERSHIP_IS_MANDATORY"
    )


@pytest.mark.parametrize("root", ["signals", "entry_triggers"])
def test_signal_and_entry_trigger_without_explicit_required_are_optional(root):
    plan, policies, profiles = _candidate_validation_fixture()
    _install_executable_policy_semantics(policies)
    source = deepcopy(plan.execution_payload["source_document"])
    condition = {
        "field": "adx",
        "operator": ">=",
        "value": 20,
    }
    if root == "entry_triggers":
        condition = {
            "type": "threshold",
            "indicator": "adx",
            "operator": ">=",
            "value": 20,
        }
    source[root]["conditions"] = [condition]
    candidate = deepcopy(source)
    candidate[root]["conditions"][0]["value"] = 30
    plan.execution_payload["source_document"] = source
    plan.execution_payload["candidate_document"] = candidate
    profiles[0].config = source
    plan.proposed_diff = [{
        "op": "replace",
        "path": f"/{root}/conditions/0/value",
        "old_value": 20,
        "value": 30,
        "reason": "runtime required default regression",
        "evidence_refs": [str(uuid.uuid4())],
    }]

    result = service._candidate_validation_result(plan, policies, profiles)

    assert result["strategy_validation"] == "VETO"
    comparison = next(
        item for item in result["policy_semantic_evidence"]["strategy"]["comparisons"]
        if item["concept"] == "adx"
    )
    assert comparison["candidate_assertion"]["required"] is False
    assert comparison["candidate_assertion"]["required_basis"] == (
        "RUNTIME_DEFAULT_REQUIRED_FALSE"
    )
    assert "ASSERTION_IS_OPTIONAL" in comparison["reason"]


@pytest.mark.parametrize(
    ("extra_key", "extra_value"),
    [("period", 14), ("timeframe", "5m")],
)
def test_global_floor_allows_unchanged_explicit_period_or_timeframe(
    extra_key,
    extra_value,
):
    plan, policies, profiles = _candidate_validation_fixture()
    _install_executable_policy_semantics(policies)
    source = deepcopy(plan.execution_payload["source_document"])
    source["filters"]["conditions"] = [{
        "field": "adx",
        "operator": ">=",
        "value": 20,
        extra_key: extra_value,
    }]
    candidate = deepcopy(source)
    candidate["filters"]["conditions"][0]["value"] = 30
    plan.execution_payload["source_document"] = source
    plan.execution_payload["candidate_document"] = candidate
    profiles[0].config = source
    plan.proposed_diff = [{
        "op": "replace",
        "path": "/filters/conditions/0/value",
        "old_value": 20,
        "value": 30,
        "reason": "period/timeframe semantic regression",
        "evidence_refs": [str(uuid.uuid4())],
    }]

    result = service._candidate_validation_result(plan, policies, profiles)

    assert result["strategy_validation"] == "PASS"
    assert result["decision"] == "PASS"
    comparison = next(
        item for item in result["policy_semantic_evidence"]["strategy"]["comparisons"]
        if item["concept"] == "adx"
    )
    assert comparison["floor_scope"] == "GLOBAL_ALL_TIMEFRAMES_PERIODS"
    assert comparison["candidate_assertion"][extra_key] == extra_value


@pytest.mark.parametrize(
    ("field", "operator", "old_value", "new_value", "expected"),
    [
        ("taker_ratio", ">=", 0.52, 0.58, "PASS"),
        ("taker_ratio", ">=", 0.52, 0.50, "VETO"),
        ("rsi", "<=", 70, 65, "PASS"),
        ("rsi", "<=", 70, 75, "VETO"),
    ],
)
def test_no_floor_numeric_change_must_be_a_monotonic_eligibility_subset(
    field,
    operator,
    old_value,
    new_value,
    expected,
):
    plan, policies, profiles = _candidate_validation_fixture()
    _install_executable_policy_semantics(policies)
    source = deepcopy(plan.execution_payload["source_document"])
    source["filters"]["conditions"] = [{
        "field": field,
        "operator": operator,
        "value": old_value,
    }]
    candidate = deepcopy(source)
    candidate["filters"]["conditions"][0]["value"] = new_value
    plan.execution_payload["source_document"] = source
    plan.execution_payload["candidate_document"] = candidate
    profiles[0].config = source
    plan.proposed_diff = [{
        "op": "replace",
        "path": "/filters/conditions/0/value",
        "old_value": old_value,
        "value": new_value,
        "reason": "monotonic eligibility test",
        "evidence_refs": [str(uuid.uuid4())],
    }]

    result = service._candidate_validation_result(plan, policies, profiles)

    assert result["risk_validation"] == "PASS"
    assert result["strategy_validation"] == expected
    assert result["decision"] == expected
    comparison = next(
        item for item in result["policy_semantic_evidence"]["strategy"]["comparisons"]
        if item["concept"] == field
    )
    assert comparison["proof_mode"] == (
        "NO_ACTIVE_FLOOR_MONOTONIC_ELIGIBILITY_RESTRICTION"
    )
    assert comparison["source_assertion"]["value"] == old_value
    assert comparison["candidate_assertion"]["value"] == new_value
    if expected == "PASS":
        assert result["policy_semantic_evidence"]["strategy"]["basis"] == (
            "NO_ACTIVE_FLOOR_MONOTONIC_ELIGIBILITY_RESTRICTION"
        )
    else:
        assert "CANDIDATE_LOOSENS_ELIGIBILITY" in comparison["reason"]


@pytest.mark.parametrize(
    ("leaf", "old_value", "new_value", "expected"),
    [
        ("min", 40, 45, "PASS"),
        ("min", 40, 35, "VETO"),
        ("max", 70, 65, "PASS"),
        ("max", 70, 75, "VETO"),
    ],
)
def test_no_floor_between_change_must_narrow_the_existing_range(
    leaf,
    old_value,
    new_value,
    expected,
):
    plan, policies, profiles = _candidate_validation_fixture()
    _install_executable_policy_semantics(policies)
    source = deepcopy(plan.execution_payload["source_document"])
    source["filters"]["conditions"] = [{
        "field": "rsi",
        "operator": "between",
        "min": 40,
        "max": 70,
    }]
    candidate = deepcopy(source)
    candidate["filters"]["conditions"][0][leaf] = new_value
    plan.execution_payload["source_document"] = source
    plan.execution_payload["candidate_document"] = candidate
    profiles[0].config = source
    plan.proposed_diff = [{
        "op": "replace",
        "path": f"/filters/conditions/0/{leaf}",
        "old_value": old_value,
        "value": new_value,
        "reason": "monotonic range test",
        "evidence_refs": [str(uuid.uuid4())],
    }]

    result = service._candidate_validation_result(plan, policies, profiles)

    assert result["strategy_validation"] == expected
    assert result["decision"] == expected


def test_no_floor_monotonic_change_allows_unchanged_period_context():
    plan, policies, profiles = _candidate_validation_fixture()
    _install_executable_policy_semantics(policies)
    source = deepcopy(plan.execution_payload["source_document"])
    source["filters"]["conditions"] = [{
        "field": "taker_ratio",
        "operator": ">=",
        "value": 0.52,
        "period": 20,
    }]
    candidate = deepcopy(source)
    candidate["filters"]["conditions"][0]["value"] = 0.58
    plan.execution_payload["source_document"] = source
    plan.execution_payload["candidate_document"] = candidate
    profiles[0].config = source
    plan.proposed_diff = [{
        "op": "replace",
        "path": "/filters/conditions/0/value",
        "old_value": 0.52,
        "value": 0.58,
        "reason": "same period monotonic test",
        "evidence_refs": [str(uuid.uuid4())],
    }]

    result = service._candidate_validation_result(plan, policies, profiles)

    assert result["decision"] == "PASS"
    comparison = result["policy_semantic_evidence"]["strategy"]["comparisons"][0]
    assert comparison["source_assertion"]["period"] == 20
    assert comparison["candidate_assertion"]["period"] == 20


@pytest.mark.parametrize(
    "field",
    [
        "adx_min",
        "adx_min_threshold",
        "entry_adx_min",
        "volume_spike_multiplier",
        "ADX",
        " adx ",
        "volume-spike",
        "volume.spike",
        "Volume Spike",
    ],
)
def test_policy_alias_or_normalized_candidate_field_never_claims_runtime_floor(field):
    plan, policies, profiles = _candidate_validation_fixture()
    _install_executable_policy_semantics(policies)
    _set_profile_condition_change(
        plan,
        profiles[0],
        field=field,
        old_value=20,
        new_value=30,
    )

    result = service._candidate_validation_result(plan, policies, profiles)

    assert result["strategy_validation"] == "VETO"
    comparison = result["policy_semantic_evidence"]["strategy"]["comparisons"][0]
    assert comparison["candidate_assertion"]["raw_concept"] == field
    assert "LITERAL_RUNTIME_FIELD_IS_NOT_PROVEN" in comparison["reason"]


def test_signals_change_is_vetoed_when_entry_triggers_shadow_it_at_runtime():
    plan, policies, profiles = _candidate_validation_fixture()
    _install_executable_policy_semantics(policies)
    source = deepcopy(plan.execution_payload["source_document"])
    source["signals"]["conditions"] = [{
        "field": "adx",
        "operator": ">=",
        "value": 20,
        "required": True,
    }]
    source["entry_triggers"]["conditions"] = [{
        "type": "threshold",
        "indicator": "taker_ratio",
        "operator": ">=",
        "value": 0.52,
        "required": True,
        "enabled": True,
    }]
    candidate = deepcopy(source)
    candidate["signals"]["conditions"][0]["value"] = 30
    plan.execution_payload["source_document"] = source
    plan.execution_payload["candidate_document"] = candidate
    profiles[0].config = source
    plan.proposed_diff = [{
        "op": "replace",
        "path": "/signals/conditions/0/value",
        "old_value": 20,
        "value": 30,
        "reason": "runtime precedence regression",
        "evidence_refs": [str(uuid.uuid4())],
    }]

    result = service._candidate_validation_result(plan, policies, profiles)

    assert result["strategy_validation"] == "VETO"
    comparison = next(
        item for item in result["policy_semantic_evidence"]["strategy"]["comparisons"]
        if item["concept"] == "adx"
    )
    assert comparison["candidate_assertion"]["runtime_gate_effective"] is False
    assert "SIGNALS_SHADOWED_BY_ENTRY_TRIGGERS" in comparison["reason"]


@pytest.mark.parametrize("root", ["filters", "signals", "entry_triggers"])
def test_profile_condition_rejects_ambiguous_dual_runtime_lookup_keys(root):
    plan, policies, profiles = _candidate_validation_fixture()
    _install_executable_policy_semantics(policies)
    source = deepcopy(plan.execution_payload["source_document"])
    condition = {
        "field": "rsi",
        "indicator": "adx",
        "operator": ">=",
        "value": 20,
    }
    if root == "entry_triggers":
        condition.update({
            "type": "threshold",
            "required": True,
            "enabled": True,
        })
    source[root]["conditions"] = [condition]
    candidate = deepcopy(source)
    candidate[root]["conditions"][0]["value"] = 30
    plan.execution_payload["source_document"] = source
    plan.execution_payload["candidate_document"] = candidate
    profiles[0].config = source
    plan.proposed_diff = [{
        "op": "replace",
        "path": f"/{root}/conditions/0/value",
        "old_value": 20,
        "value": 30,
        "reason": "dual lookup regression",
        "evidence_refs": [str(uuid.uuid4())],
    }]

    result = service._candidate_validation_result(plan, policies, profiles)

    assert result["decision"] == "VETO"
    schema_check = next(
        item for item in result["checks"]
        if item["check"] == "PROFILE_CANDIDATE_SCHEMA"
    )
    assert schema_check["decision"] == "VETO"
    assert (
        "only lookup key" in schema_check["reason"]
        or "must use indicator only" in schema_check["reason"]
    )


def test_signals_between_bounds_are_not_claimed_when_runtime_drops_them():
    plan, policies, profiles = _candidate_validation_fixture()
    _install_executable_policy_semantics(policies)
    source = deepcopy(plan.execution_payload["source_document"])
    source["signals"]["conditions"] = [{
        "field": "adx",
        "operator": "between",
        "min": 20,
        "max": 70,
        "required": True,
    }]
    candidate = deepcopy(source)
    candidate["signals"]["conditions"][0]["min"] = 30
    plan.execution_payload["source_document"] = source
    plan.execution_payload["candidate_document"] = candidate
    profiles[0].config = source
    plan.proposed_diff = [{
        "op": "replace",
        "path": "/signals/conditions/0/min",
        "old_value": 20,
        "value": 30,
        "reason": "signal conversion regression",
        "evidence_refs": [str(uuid.uuid4())],
    }]

    result = service._candidate_validation_result(plan, policies, profiles)

    assert result["strategy_validation"] == "VETO"
    comparison = next(
        item for item in result["policy_semantic_evidence"]["strategy"]["comparisons"]
        if item["concept"] == "adx"
    )
    assert comparison["candidate_assertion"]["runtime_gate_effective"] is False
    assert "SIGNALS_BETWEEN_OR_COMPARISON_NOT_PROPAGATED" in comparison["reason"]


def test_entry_comparison_rejects_scalar_threshold_ignored_by_runtime():
    plan, policies, profiles = _candidate_validation_fixture()
    _install_executable_policy_semantics(policies)
    source = deepcopy(plan.execution_payload["source_document"])
    source["entry_triggers"]["conditions"] = [{
        "type": "comparison",
        "left": "adx",
        "right": "rsi",
        "operator": ">=",
        "value": 20,
        "required": True,
        "enabled": True,
    }]
    candidate = deepcopy(source)
    candidate["entry_triggers"]["conditions"][0]["value"] = 30
    plan.execution_payload["source_document"] = source
    plan.execution_payload["candidate_document"] = candidate
    profiles[0].config = source
    plan.proposed_diff = [{
        "op": "replace",
        "path": "/entry_triggers/conditions/0/value",
        "old_value": 20,
        "value": 30,
        "reason": "comparison scalar regression",
        "evidence_refs": [str(uuid.uuid4())],
    }]

    result = service._candidate_validation_result(plan, policies, profiles)

    assert result["decision"] == "VETO"
    schema_check = next(
        item for item in result["checks"]
        if item["check"] == "PROFILE_CANDIDATE_SCHEMA"
    )
    assert schema_check["decision"] == "VETO"
    assert "comparison can contain only left/right operands" in schema_check["reason"]


def test_block_comparison_condition_with_ordering_operator_is_not_falsely_vetoed():
    plan, policies, profiles = _candidate_validation_fixture()
    _install_executable_policy_semantics(policies)
    source = deepcopy(plan.execution_payload["source_document"])
    source["block_rules"] = {"blocks": [{
        "name": "Estrutura EMA bearish",
        "logic": "AND",
        "enabled": True,
        "conditions": [{
            "type": "comparison",
            "left": "price",
            "right": "ema50",
            "operator": "<",
        }],
    }]}
    candidate = deepcopy(source)
    candidate["default_timeframe"] = "15m"
    plan.execution_payload["source_document"] = source
    plan.execution_payload["candidate_document"] = candidate
    profiles[0].config = source
    plan.proposed_diff = [{
        "op": "replace",
        "path": "/default_timeframe",
        "old_value": "5m",
        "value": "15m",
        "reason": "unrelated change on a profile with a comparison block condition",
        "evidence_refs": [str(uuid.uuid4())],
    }]

    result = service._candidate_validation_result(plan, policies, profiles)

    schema_check = next(
        item for item in result["checks"]
        if item["check"] == "PROFILE_CANDIDATE_SCHEMA"
    )
    assert schema_check["decision"] == "PASS"


def test_unbound_compatible_profile_uses_conservative_global_floor_and_fence():
    plan, policies, profiles = _candidate_validation_fixture()
    _install_executable_policy_semantics(policies)
    _set_profile_condition_change(
        plan,
        profiles[0],
        field="adx",
        old_value=20,
        new_value=30,
    )

    result = service._candidate_validation_result(plan, policies, profiles)

    assert result["strategy_validation"] == "PASS"
    assert result["decision"] == "PASS"
    evidence = result["policy_semantic_evidence"]["strategy"]
    assert evidence["binding_authority"] == "EVIDENCE_ONLY_NOT_AUTHORIZATION"
    assert evidence["profile_strategy_bindings"][0]["strategy_ids"] == []
    assert evidence["basis"] == "CONSERVATIVE_GLOBAL_FLOOR_COMPATIBILITY"
    plan.evidence["candidate_validation"] = result
    service._require_persisted_candidate_pass(plan, result)


@pytest.mark.parametrize(
    "case",
    ["or_logic", "optional", "period", "timeframe", "operator", "selected_rule"],
)
def test_conservative_global_floor_rejects_optional_or_structural_changes(case):
    plan, policies, profiles = _candidate_validation_fixture()
    _install_executable_policy_semantics(policies)
    source = deepcopy(plan.execution_payload["source_document"])
    source["filters"]["conditions"] = [{
        "field": "adx",
        "operator": ">=",
        "value": 20,
    }]
    candidate = deepcopy(source)
    candidate["filters"]["conditions"][0]["value"] = 30
    path = "/filters/conditions/0/value"
    old_value = 20
    new_value = 30

    if case == "or_logic":
        source["filters"]["logic"] = "OR"
        candidate["filters"]["logic"] = "OR"
    elif case == "optional":
        source["filters"]["conditions"][0]["required"] = False
        candidate["filters"]["conditions"][0]["required"] = False
    elif case == "period":
        source["filters"]["conditions"][0].update({"value": 30, "period": 14})
        candidate = deepcopy(source)
        candidate["filters"]["conditions"][0]["period"] = 20
        path, old_value, new_value = "/filters/conditions/0/period", 14, 20
    elif case == "timeframe":
        source["filters"]["conditions"][0].update({
            "value": 30,
            "timeframe": "5m",
        })
        candidate = deepcopy(source)
        candidate["filters"]["conditions"][0]["timeframe"] = "15m"
        path, old_value, new_value = (
            "/filters/conditions/0/timeframe", "5m", "15m"
        )
    elif case == "operator":
        source["filters"]["conditions"][0]["value"] = 30
        candidate = deepcopy(source)
        candidate["filters"]["conditions"][0]["operator"] = ">"
        path, old_value, new_value = "/filters/conditions/0/operator", ">=", ">"
    elif case == "selected_rule":
        source = deepcopy(plan.execution_payload["source_document"])
        candidate = deepcopy(source)
        old_value = source["scoring"]["selected_rule_ids"][0]
        candidate["scoring"]["selected_rule_ids"] = []
        path, new_value = "/scoring/selected_rule_ids/0", None

    plan.execution_payload["source_document"] = source
    plan.execution_payload["candidate_document"] = candidate
    profiles[0].config = source
    plan.proposed_diff = [{
        "op": "remove" if case == "selected_rule" else "replace",
        "path": path,
        "old_value": old_value,
        **({} if case == "selected_rule" else {"value": new_value}),
        "reason": "policy structure test",
        "evidence_refs": [str(uuid.uuid4())],
    }]

    result = service._candidate_validation_result(plan, policies, profiles)

    assert result["risk_validation"] == "PASS"
    assert result["strategy_validation"] == "VETO"
    assert result["decision"] == "VETO"
    assert result["policy_semantic_evidence"]["strategy"]["validation_mode"] == (
        "CONSERVATIVE_GLOBAL_FLOOR_COMPATIBILITY"
    )


def test_conservative_global_floor_uses_maximum_of_all_active_floors(monkeypatch):
    contracts = deepcopy(service._STRATEGY_PARAMETER_CONTRACTS)
    contracts["institutional_momentum"] = {
        "adx_min": {
            "concept": "adx",
            "comparison": "minimum",
            "runtime_basis": "PROFILE_SCORE_ADX_THRESHOLD",
        },
    }
    monkeypatch.setattr(service, "_STRATEGY_PARAMETER_CONTRACTS", contracts)
    plan, policies, profiles = _candidate_validation_fixture()
    _install_executable_policy_semantics(policies)
    strategy = next(item for item in policies if item.config_type == "strategy")
    strategy.config_json["strategies"].append({
        "id": "institutional_momentum",
        "name": "Institutional Momentum",
        "enabled": True,
        "params": {"adx_min": 35},
    })

    for new_value, expected in ((30, "VETO"), (40, "PASS")):
        _set_profile_condition_change(
            plan,
            profiles[0],
            field="adx",
            old_value=20,
            new_value=new_value,
        )
        result = service._candidate_validation_result(plan, policies, profiles)
        comparison = next(
            item for item in result["policy_semantic_evidence"]["strategy"]["comparisons"]
            if item["concept"] == "adx"
        )
        assert comparison["effective_global_minimum"] == 35
        assert sorted(
            item["persisted_value"]
            for item in comparison["active_floor_constraints"]
        ) == [25, 35]
        assert result["strategy_validation"] == expected
        assert result["decision"] == expected


@pytest.mark.parametrize("field", ["rsi", "lookback"])
def test_touched_concept_without_proven_active_direction_fails_closed(field):
    plan, policies, profiles = _candidate_validation_fixture()
    _install_executable_policy_semantics(policies)
    strategy = next(item for item in policies if item.config_type == "strategy")
    strategy.config_json["strategies"][1]["enabled"] = True
    _set_profile_condition_change(
        plan,
        profiles[0],
        field=field,
        old_value=20,
        new_value=40,
    )

    result = service._candidate_validation_result(plan, policies, profiles)

    assert result["strategy_validation"] == "VETO"
    comparison = next(
        item for item in result["policy_semantic_evidence"]["strategy"]["comparisons"]
        if item["concept"] == field
    )
    assert comparison["decision"] == "VETO"
    assert "MAPPING_NOT_PROVEN" in comparison["reason"]


def test_unknown_active_strategy_parameter_fails_closed():
    plan, policies, profiles = _candidate_validation_fixture()
    _install_executable_policy_semantics(policies)
    strategy = next(item for item in policies if item.config_type == "strategy")
    strategy.config_json["strategies"][0]["params"]["unknown_active_floor"] = 7

    result = service._candidate_validation_result(plan, policies, profiles)

    assert result["strategy_validation"] == "VETO"
    assert result["decision"] == "VETO"
    assert "must contain exactly" in result["policy_semantic_evidence"]["strategy"]["basis"]


def test_profile_candidate_cannot_reference_protected_risk_domains():
    plan, policies, profiles = _candidate_validation_fixture()
    _install_executable_policy_semantics(policies)
    _set_profile_condition_change(
        plan,
        profiles[0],
        field="leverage",
        old_value=2,
        new_value=3,
    )

    result = service._candidate_validation_result(plan, policies, profiles)

    assert result["risk_validation"] == "VETO"
    assert "protected risk concepts" in result["policy_semantic_evidence"]["risk"]["basis"]
    assert result["decision"] == "VETO"


def test_execution_fence_detects_strategy_policy_drift_after_semantic_pass():
    plan, policies, profiles = _candidate_validation_fixture()
    _install_executable_policy_semantics(policies)
    profiles[0].name = "Momentum Breakout"
    _set_profile_condition_change(
        plan,
        profiles[0],
        field="adx",
        old_value=20,
        new_value=25,
    )
    stored = service._candidate_validation_result(plan, policies, profiles)
    assert stored["decision"] == "PASS"
    plan.evidence["candidate_validation"] = stored
    strategy = next(item for item in policies if item.config_type == "strategy")
    strategy.config_json["strategies"][0]["params"]["adx_min"] = 30
    current = service._candidate_validation_result(plan, policies, profiles)
    assert current["strategy_validation"] == "VETO"

    with pytest.raises(
        service.GovernedExecutionFenceError,
        match="ANALYSIS_CHAT_CANDIDATE_VALIDATION_STALE",
    ):
        service._require_persisted_candidate_pass(plan, current)


def test_candidate_aware_validator_vetoes_unknown_score_rule_reference():
    plan, policies, profiles = _candidate_validation_fixture(
        selected_rule_ids=["rule-not-persisted"]
    )

    result = service._candidate_validation_result(plan, policies, profiles)

    assert result["decision"] == "VETO"
    assert result["terminal_reason"] == "GLOBAL_RISK_VETO"
    score_check = next(
        item for item in result["checks"]
        if item["check"] == "PROFILE_SCORE_LINKS"
    )
    assert score_check["decision"] == "VETO"
    assert "do not exist globally" in score_check["reason"]


def test_candidate_aware_validator_vetoes_a_candidate_diff_mismatch():
    plan, policies, profiles = _candidate_validation_fixture()
    plan.execution_payload["candidate_document"]["default_timeframe"] = "1h"

    result = service._candidate_validation_result(plan, policies, profiles)

    assert result["decision"] == "VETO"
    diff_check = next(
        item for item in result["checks"]
        if item["check"] == "MATERIALIZED_DIFF_MATCHES_CANDIDATE"
    )
    assert diff_check["decision"] == "VETO"
    assert "not reconstructed from the materialized diff" in diff_check["reason"]


def test_candidate_aware_validator_vetoes_an_unreviewed_hidden_candidate_change():
    plan, policies, profiles = _candidate_validation_fixture()
    plan.execution_payload["candidate_document"]["hidden_live_override"] = True

    result = service._candidate_validation_result(plan, policies, profiles)

    assert result["decision"] == "VETO"
    check = next(
        item for item in result["checks"]
        if item["check"] == "MATERIALIZED_DIFF_MATCHES_CANDIDATE"
    )
    assert check["decision"] == "VETO"
    assert "not reconstructed from the materialized diff" in check["reason"]


def test_candidate_aware_validator_reconstructs_bulk_candidate_as_a_whole():
    plan, policies, profiles = _candidate_validation_fixture()
    profile = profiles[0]
    profile_id = str(profile.id)
    source = plan.execution_payload["source_document"]
    candidate = plan.execution_payload["candidate_document"]
    plan.execution_payload = {
        "operation_type": "UPDATE_PROFILE_CONFIG_SET",
        "profile_ids": [profile_id],
        "source_document": {"profiles": [{
            "profile_id": profile_id,
            "profile_name": "Profile A",
            "config": source,
        }]},
        "candidate_document": {"profiles": [{
            "profile_id": profile_id,
            "profile_name": "Profile A",
            "config": {**candidate, "hidden_live_override": True},
        }]},
    }
    plan.proposed_diff = [{
        "op": "replace",
        "path": f"/profiles/{profile_id}/default_timeframe",
        "old_value": "5m",
        "value": "15m",
        "profile_id": profile_id,
        "profile_name": "Profile A",
    }]

    result = service._candidate_validation_result(plan, policies, profiles)

    assert result["decision"] == "VETO"
    check = next(
        item for item in result["checks"]
        if item["check"] == "MATERIALIZED_DIFF_MATCHES_CANDIDATE"
    )
    assert check["decision"] == "VETO"


@pytest.mark.parametrize(
    ("path", "old_value", "value", "reason_fragment"),
    [
        ("/default_timeframe", "5m", "2m", "default_timeframe is unsupported"),
        ("/filters/logic", "AND", "XOR", "must be AND or OR"),
    ],
)
def test_candidate_aware_validator_vetoes_invalid_strict_profile_contract(
    path, old_value, value, reason_fragment,
):
    plan, policies, profiles = _candidate_validation_fixture()
    source = plan.execution_payload["source_document"]
    candidate, _ = service.apply_typed_patch(source, [{
        "op": "replace",
        "path": path,
        "old_value": old_value,
        "value": value,
        "array_guards": [],
    }], allowed_roots=service.PROFILE_ROOTS)
    plan.execution_payload["candidate_document"] = candidate
    plan.proposed_diff = [{
        "op": "replace",
        "path": path,
        "old_value": old_value,
        "value": value,
    }]

    result = service._candidate_validation_result(plan, policies, profiles)

    assert result["decision"] == "VETO"
    check = next(
        item for item in result["checks"]
        if item["check"] == "PROFILE_CANDIDATE_SCHEMA"
    )
    assert check["decision"] == "VETO"
    assert reason_fragment in check["reason"]


def test_candidate_aware_validator_vetoes_non_numeric_threshold_operator_value():
    plan, policies, profiles = _candidate_validation_fixture()
    source = service._validate_profile_config({
        "default_timeframe": "5m",
        "filters": {
            "logic": "AND",
            "conditions": [{"field": "adx", "operator": ">=", "value": 18}],
        },
        "scoring": {"enabled": True, "selected_rule_ids": ["rule-adx"]},
    })
    candidate = deepcopy(source)
    candidate["filters"]["conditions"][0]["value"] = "22"
    plan.execution_payload["source_document"] = source
    plan.execution_payload["candidate_document"] = candidate
    plan.proposed_diff = [{
        "op": "replace",
        "path": "/filters/conditions/0/value",
        "old_value": 18,
        "value": "22",
    }]

    result = service._candidate_validation_result(plan, policies, profiles)

    assert result["decision"] == "VETO"
    check = next(
        item for item in result["checks"]
        if item["check"] == "PROFILE_CANDIDATE_SCHEMA"
    )
    assert "must be numeric" in check["reason"]


@pytest.mark.parametrize(
    ("leaf", "old_value", "value", "reason_fragment"),
    [
        ("operator", ">=", "approximately", "operator is unsupported"),
        ("required", True, "yes", "required must be boolean"),
    ],
)
def test_candidate_aware_validator_vetoes_invalid_operator_or_boolean_structure(
    leaf, old_value, value, reason_fragment,
):
    plan, policies, profiles = _candidate_validation_fixture()
    source = service._validate_profile_config({
        "default_timeframe": "5m",
        "entry_triggers": {
            "logic": "AND",
            "conditions": [{
                "id": "entry-rsi",
                "type": "threshold",
                "indicator": "rsi",
                "operator": ">=",
                "value": 45,
                "required": True,
                "enabled": True,
            }],
        },
        "scoring": {"enabled": True, "selected_rule_ids": ["rule-adx"]},
    })
    candidate = deepcopy(source)
    candidate["entry_triggers"]["conditions"][0][leaf] = value
    plan.execution_payload["source_document"] = source
    plan.execution_payload["candidate_document"] = candidate
    plan.proposed_diff = [{
        "op": "replace",
        "path": f"/entry_triggers/conditions/0/{leaf}",
        "old_value": old_value,
        "value": value,
    }]

    result = service._candidate_validation_result(plan, policies, profiles)

    assert result["decision"] == "VETO"
    check = next(
        item for item in result["checks"]
        if item["check"] == "PROFILE_CANDIDATE_SCHEMA"
    )
    assert reason_fragment in check["reason"]


def test_candidate_validator_keeps_legacy_disable_schema_safe_but_policy_blocked():
    plan, policies, profiles = _candidate_validation_fixture()
    profile = profiles[0]
    profile.config = {"default_timeframe": "5m"}
    profile_id = str(profile.id)
    plan.execution_payload = {
        "operation_type": "SET_PROFILE_ACTIVE_STATUS",
        "profile_ids": [profile_id],
        "source_document": {"profiles": [{
            "profile_id": profile_id,
            "profile_name": "Profile A",
            "is_active": True,
        }]},
        "candidate_document": {"profiles": [{
            "profile_id": profile_id,
            "profile_name": "Profile A",
            "is_active": False,
        }]},
    }
    plan.proposed_diff = [{
        "op": "replace",
        "path": f"/profiles/{profile_id}/is_active",
        "old_value": True,
        "value": False,
        "profile_id": profile_id,
        "profile_name": "Profile A",
    }]

    result = service._candidate_validation_result(plan, policies, profiles)

    assert result["decision"] == "VETO"
    check = next(
        item for item in result["checks"]
        if item["check"] == "PROFILE_CANDIDATE_SCHEMA"
    )
    assert check["decision"] == "PASS"


def test_profile_deactivation_vetoes_until_watchlist_consumers_honor_is_active():
    plan, policies, profiles = _candidate_validation_fixture()
    _install_executable_policy_semantics(policies)
    profile = profiles[0]
    profile_id = str(profile.id)
    plan.execution_payload = {
        "operation_type": "SET_PROFILE_ACTIVE_STATUS",
        "profile_ids": [profile_id],
        "source_document": {"profiles": [{
            "profile_id": profile_id,
            "profile_name": profile.name,
            "is_active": True,
        }]},
        "candidate_document": {"profiles": [{
            "profile_id": profile_id,
            "profile_name": profile.name,
            "is_active": False,
        }]},
    }
    plan.proposed_diff = [{
        "op": "replace",
        "path": f"/profiles/{profile_id}/is_active",
        "old_value": True,
        "value": False,
        "profile_id": profile_id,
        "profile_name": profile.name,
    }]

    result = service._candidate_validation_result(plan, policies, profiles)

    # pipeline_scan resolves referenced watchlist profile_ids without filtering
    # Profile.is_active, so false cannot yet prove a global authority reduction.
    assert result["decision"] == "VETO"
    assert result["strategy_validation"] == "VETO"
    assert result["policy_semantic_evidence"]["strategy"]["basis"] == (
        "PROFILE_DEACTIVATION_NOT_GLOBALLY_ENFORCED_BY_WATCHLIST_CONSUMERS"
    )


def test_mixed_deactivation_and_activation_status_change_fails_closed():
    plan, policies, profiles = _candidate_validation_fixture()
    _install_executable_policy_semantics(policies)
    active = profiles[0]
    inactive = SimpleNamespace(
        id=uuid.uuid4(),
        name="Profile B",
        profile_role="acquisition_queue",
        pipeline_label="L3_PROFILE_B",
        generated_by=None,
        config=deepcopy(active.config),
        is_active=False,
        profile_version=active.profile_version,
        updated_at=active.updated_at,
    )
    profiles.append(inactive)
    active_id, inactive_id = str(active.id), str(inactive.id)
    plan.execution_payload = {
        "operation_type": "SET_PROFILE_ACTIVE_STATUS",
        "profile_ids": [active_id, inactive_id],
        "source_document": {"profiles": [
            {"profile_id": active_id, "profile_name": active.name, "is_active": True},
            {"profile_id": inactive_id, "profile_name": inactive.name, "is_active": False},
        ]},
        "candidate_document": {"profiles": [
            {"profile_id": active_id, "profile_name": active.name, "is_active": False},
            {"profile_id": inactive_id, "profile_name": inactive.name, "is_active": True},
        ]},
    }
    plan.proposed_diff = [
        {
            "op": "replace",
            "path": f"/profiles/{active_id}/is_active",
            "old_value": True,
            "value": False,
            "profile_id": active_id,
            "profile_name": active.name,
        },
        {
            "op": "replace",
            "path": f"/profiles/{inactive_id}/is_active",
            "old_value": False,
            "value": True,
            "profile_id": inactive_id,
            "profile_name": inactive.name,
        },
    ]

    result = service._candidate_validation_result(plan, policies, profiles)

    assert result["strategy_validation"] == "VETO"
    assert result["decision"] == "VETO"
    assert result["policy_semantic_evidence"]["strategy"]["validation_mode"] == (
        "PROFILE_ACTIVATION_OR_MIXED_STATUS_CHANGE_NOT_PROVEN"
    )


def test_candidate_validator_vetoes_activating_profile_with_unknown_score_link():
    plan, policies, profiles = _candidate_validation_fixture()
    profile = profiles[0]
    profile.is_active = False
    profile.config = service._validate_profile_config({
        "default_timeframe": "5m",
        "scoring": {
            "enabled": True,
            "selected_rule_ids": ["rule-not-persisted"],
        },
    })
    profile_id = str(profile.id)
    plan.execution_payload = {
        "operation_type": "SET_PROFILE_ACTIVE_STATUS",
        "profile_ids": [profile_id],
        "source_document": {"profiles": [{
            "profile_id": profile_id,
            "profile_name": "Profile A",
            "is_active": False,
        }]},
        "candidate_document": {"profiles": [{
            "profile_id": profile_id,
            "profile_name": "Profile A",
            "is_active": True,
        }]},
    }
    plan.proposed_diff = [{
        "op": "replace",
        "path": f"/profiles/{profile_id}/is_active",
        "old_value": False,
        "value": True,
        "profile_id": profile_id,
        "profile_name": "Profile A",
    }]

    result = service._candidate_validation_result(plan, policies, profiles)

    assert result["decision"] == "VETO"
    score_check = next(
        item for item in result["checks"]
        if item["check"] == "PROFILE_SCORE_LINKS"
    )
    assert score_check["decision"] == "VETO"
    assert "do not exist globally" in score_check["reason"]


def test_candidate_aware_validator_vetoes_activating_a_legacy_invalid_profile():
    plan, policies, profiles = _candidate_validation_fixture()
    profile = profiles[0]
    profile.config = {"default_timeframe": "5m"}
    profile.is_active = False
    profile_id = str(profile.id)
    plan.execution_payload = {
        "operation_type": "SET_PROFILE_ACTIVE_STATUS",
        "profile_ids": [profile_id],
        "source_document": {"profiles": [{
            "profile_id": profile_id,
            "profile_name": "Profile A",
            "is_active": False,
        }]},
        "candidate_document": {"profiles": [{
            "profile_id": profile_id,
            "profile_name": "Profile A",
            "is_active": True,
        }]},
    }
    plan.proposed_diff = [{
        "op": "replace",
        "path": f"/profiles/{profile_id}/is_active",
        "old_value": False,
        "value": True,
        "profile_id": profile_id,
        "profile_name": "Profile A",
    }]

    result = service._candidate_validation_result(plan, policies, profiles)

    assert result["decision"] == "VETO"
    check = next(
        item for item in result["checks"]
        if item["check"] == "PROFILE_CANDIDATE_SCHEMA"
    )
    assert check["decision"] == "VETO"


def test_candidate_aware_validator_reconstructs_status_candidate_as_a_whole():
    plan, policies, profiles = _candidate_validation_fixture()
    profile_id = str(profiles[0].id)
    plan.execution_payload = {
        "operation_type": "SET_PROFILE_ACTIVE_STATUS",
        "profile_ids": [profile_id],
        "source_document": {"profiles": [{
            "profile_id": profile_id,
            "profile_name": "Profile A",
            "is_active": True,
        }]},
        "candidate_document": {"profiles": [{
            "profile_id": profile_id,
            "profile_name": "Profile A",
            "is_active": False,
            "hidden_live_override": True,
        }]},
    }
    plan.proposed_diff = [{
        "op": "replace",
        "path": f"/profiles/{profile_id}/is_active",
        "old_value": True,
        "value": False,
        "profile_id": profile_id,
        "profile_name": "Profile A",
    }]

    result = service._candidate_validation_result(plan, policies, profiles)

    assert result["decision"] == "VETO"
    check = next(
        item for item in result["checks"]
        if item["check"] == "MATERIALIZED_DIFF_MATCHES_CANDIDATE"
    )
    assert check["decision"] == "VETO"


@pytest.mark.parametrize("config_type", ["risk", "strategy", "score_engine", "filters"])
def test_candidate_aware_validator_vetoes_config_family_without_registered_schema(config_type):
    plan, policies, _profiles = _candidate_validation_fixture()
    source = {"enabled": True}
    candidate = {"enabled": False}
    target = next(
        (item for item in policies if item.config_type == config_type),
        None,
    )
    target_id = target.id if target is not None else uuid.uuid4()
    plan.execution_payload = {
        "operation_type": "UPDATE_CONFIG_PROFILE",
        "config_profile_id": str(target_id),
        "config_type": config_type,
        "pool_id": None,
        "source_document": source,
        "candidate_document": candidate,
    }
    plan.proposed_diff = [{
        "op": "replace", "path": "/enabled", "old_value": True, "value": False,
    }]

    result = service._candidate_validation_result(plan, policies, [])

    assert result["decision"] == "VETO"
    check = next(
        item for item in result["checks"]
        if item["check"] == "CONFIG_PROFILE_REGISTERED_GLOBAL_SCHEMA"
    )
    assert check["decision"] == "VETO"
    assert "no registered governed candidate schema" in check["reason"]


def test_candidate_aware_validator_vetoes_pool_scoped_config_target():
    plan, policies, _profiles = _candidate_validation_fixture()
    score_record = next(item for item in policies if item.config_type == "score")
    source = score_record.config_json
    candidate = deepcopy(source)
    candidate["scoring_rules"][0]["points"] = 20
    plan.execution_payload = {
        "operation_type": "UPDATE_CONFIG_PROFILE",
        "config_profile_id": str(score_record.id),
        "config_type": "score",
        "pool_id": str(uuid.uuid4()),
        "source_document": source,
        "candidate_document": candidate,
    }
    plan.proposed_diff = [{
        "op": "replace",
        "path": "/scoring_rules/0/points",
        "old_value": 25,
        "value": 20,
    }]

    result = service._candidate_validation_result(plan, policies, [])

    assert result["decision"] == "VETO"
    check = next(
        item for item in result["checks"]
        if item["check"] == "CONFIG_PROFILE_REGISTERED_GLOBAL_SCHEMA"
    )
    assert "pool_id=null" in check["reason"]


def test_candidate_aware_validator_uses_score_candidate_for_all_profile_links():
    plan, policies, _target_profiles = _candidate_validation_fixture()
    score_record = next(item for item in policies if item.config_type == "score")
    source_score = score_record.config_json
    candidate_score = deepcopy(source_score)
    candidate_score["scoring_rules"][0]["points"] = 20
    plan.execution_payload = {
        "operation_type": "UPDATE_CONFIG_PROFILE",
        "config_profile_id": str(score_record.id),
        "config_type": "score",
        "pool_id": None,
        "source_document": source_score,
        "candidate_document": candidate_score,
    }
    plan.proposed_diff = [{
        "op": "replace",
        "path": "/scoring_rules/0/points",
        "old_value": 25,
        "value": 20,
        "reason": "evidence",
        "evidence_refs": [str(uuid.uuid4())],
    }]
    profile_config = service._validate_profile_config({
        "scoring": {"enabled": True, "selected_rule_ids": ["rule-adx"]},
    })
    now = datetime(2026, 8, 13, tzinfo=timezone.utc)
    profiles = [
        SimpleNamespace(
            id=uuid.uuid4(),
            config=profile_config,
            is_active=True,
            profile_version=now,
            updated_at=now,
        ),
        SimpleNamespace(
            id=uuid.uuid4(),
            config=deepcopy(profile_config),
            is_active=False,
            profile_version=now,
            updated_at=now,
        ),
    ]

    result = service._candidate_validation_result(plan, policies, profiles)

    assert result["decision"] == "VETO"
    assert result["validation_scope"] == "CANDIDATE_SCHEMA_AND_PERSISTED_POLICY_SEMANTICS"
    assert result["policy_semantic_validation"]["risk"] == "VETO"
    assert result["policy_semantic_validation"]["strategy"] == "VETO"
    score_snapshot = next(
        item for item in result["policy_snapshots"] if item["family"] == "score"
    )
    assert score_snapshot["uses_candidate_document"] is True
    assert score_snapshot["document_hash"] == service.document_hash(candidate_score)
    score_check = next(
        item for item in result["checks"]
        if item["check"] == "PROFILE_SCORE_LINKS"
    )
    assert score_check["decision"] == "PASS"
    assert result["profile_dependency_snapshot_hash"] == service.document_hash(
        result["profile_dependency_snapshots"]
    )
    assert [item["id"] for item in result["profile_dependency_snapshots"]] == sorted(
        str(profile.id) for profile in profiles
    )


def test_profile_validator_accepts_runtime_flat_blocks_inferred_entry_and_contains():
    plan, policies, profiles = _candidate_validation_fixture()
    source = deepcopy(plan.execution_payload["source_document"])
    source["block_rules"]["blocks"] = [
        {
            "id": "preset-rsi",
            "name": "Preset RSI",
            "enabled": True,
            "indicator": "rsi",
            "type": "threshold",
            "operator": ">",
            "value": 80,
            "reason": "preset_ia",
        },
        {"indicator": "rsi", "min": 20, "max": 80},
    ]
    source["entry_triggers"] = {
        "logic": "AND",
        "conditions": [{
            "id": "entry-macd",
            "indicator": "macd_signal",
            "operator": "contains",
            "value": "bull",
            "required": True,
            "enabled": True,
        }],
    }
    candidate = deepcopy(source)
    candidate["default_timeframe"] = "15m"
    plan.execution_payload["source_document"] = source
    plan.execution_payload["candidate_document"] = candidate
    plan.proposed_diff = [{
        "op": "replace",
        "path": "/default_timeframe",
        "old_value": "5m",
        "value": "15m",
    }]

    result = service._candidate_validation_result(plan, policies, profiles)

    assert result["decision"] == "VETO"
    assert next(
        item for item in result["checks"]
        if item["check"] == "PROFILE_CANDIDATE_SCHEMA"
    )["decision"] == "PASS"


def test_profile_validator_accepts_exact_profile_intelligence_metadata_on_activation():
    plan, policies, profiles = _candidate_validation_fixture()
    profile = profiles[0]
    profile.is_active = False
    suggestion_id = uuid.uuid4()
    profile.config = {
        "signals": {
            "logic": "AND",
            "conditions": [{"field": "adx", "operator": ">=", "value": 25}],
        },
        "entry_triggers": {
            "logic": "AND",
            "conditions": [{"field": "adx", "operator": ">=", "value": 25}],
        },
        "scoring": {
            "selected_rule_ids": ["rule-adx"],
            "weights": {
                "liquidity": 25,
                "market_structure": 25,
                "momentum": 25,
                "signal": 25,
            },
            "generated_rules": [_score_document()["scoring_rules"][0]],
            "source": "profile_intelligence",
            "suggestion_id": str(suggestion_id),
        },
        "block_rules": {
            "blocks": [{"indicator": "rsi", "operator": ">", "value": 78}],
        },
        "metadata": {
            "generated_by": "profile_intelligence",
            "suggestion_id": str(suggestion_id),
            "source_combination_id": None,
            "confidence_level": "HIGH",
            "confidence_score": 0.8,
            "created_as": "SHADOW_ONLY",
            "live_trading_enabled": False,
            "is_shadow_only": True,
            "profile_family": "MOMENTUM",
        },
    }
    profile_id = str(profile.id)
    plan.execution_payload = {
        "operation_type": "SET_PROFILE_ACTIVE_STATUS",
        "profile_ids": [profile_id],
        "source_document": {"profiles": [{
            "profile_id": profile_id,
            "profile_name": "PI Profile",
            "is_active": False,
        }]},
        "candidate_document": {"profiles": [{
            "profile_id": profile_id,
            "profile_name": "PI Profile",
            "is_active": True,
        }]},
    }
    plan.proposed_diff = [{
        "op": "replace",
        "path": f"/profiles/{profile_id}/is_active",
        "old_value": False,
        "value": True,
        "profile_id": profile_id,
        "profile_name": "PI Profile",
    }]

    result = service._candidate_validation_result(plan, policies, profiles)

    assert result["decision"] == "VETO"


def test_profile_validator_rejects_unknown_runtime_keys_without_hiding_metadata():
    profile = service._validate_profile_config({
        "scoring": {"selected_rule_ids": ["rule-adx"]},
    })
    profile["entry_triggers"]["conditions"] = [{
        "indicator": "adx",
        "operator": ">=",
        "value": 25,
        "enabled": True,
        "required": True,
        "runtime_override": True,
    }]

    with pytest.raises(ValueError, match="contains unknown keys: runtime_override"):
        service._validate_strict_profile_config(profile)


@pytest.mark.parametrize(
    ("mutation", "reason_fragment"),
    [
        (lambda score: score.update({"unexpected": True}), "contain exactly"),
        (
            lambda score: score["scoring_rules"][0].update({"unexpected": True}),
            "contains unknown keys",
        ),
        (
            lambda score: score["thresholds"].update({"neutral": 90}),
            "ordered neutral <= buy <= strong_buy",
        ),
        (
            lambda score: score["thresholds"].update({"strong_buy": 101}),
            "must be between 0 and 100",
        ),
        (
            lambda score: score.update({"auto_select_top_n": True}),
            "integer between 1 and 50",
        ),
        (
            lambda score: score.update({"auto_select_top_n": 51}),
            "integer between 1 and 50",
        ),
        (
            lambda score: score.update({"auto_select_min_score": -1}),
            "must be between 0 and 100",
        ),
    ],
)
def test_score_candidate_rejects_unknown_threshold_and_top_n_false_passes(
    mutation, reason_fragment,
):
    candidate = _score_document()
    mutation(candidate)

    with pytest.raises(ValueError, match=reason_fragment):
        service._validate_config_candidate("score", candidate)


def test_candidate_gate_vetoes_an_unknown_score_document_key():
    plan, policies, profiles = _candidate_validation_fixture()
    score_record = next(item for item in policies if item.config_type == "score")
    source = deepcopy(score_record.config_json)
    candidate = deepcopy(source)
    candidate["unreviewed_override"] = True
    plan.execution_payload = {
        "operation_type": "UPDATE_CONFIG_PROFILE",
        "config_profile_id": str(score_record.id),
        "config_type": "score",
        "pool_id": None,
        "source_document": source,
        "candidate_document": candidate,
    }
    plan.proposed_diff = [{
        "op": "add",
        "path": "/unreviewed_override",
        "old_value": None,
        "value": True,
    }]

    result = service._candidate_validation_result(plan, policies, profiles)

    assert result["decision"] == "VETO"
    check = next(
        item for item in result["checks"]
        if item["check"] == "SCORE_CANDIDATE_SCHEMA"
    )
    assert check["decision"] == "VETO"


@pytest.mark.parametrize("config_type", ["spot_engine", "futures_engine"])
def test_engine_candidate_requires_complete_canonical_document(config_type):
    if config_type == "spot_engine":
        partial = {"selling": {"never_sell_at_loss": True}}
        canonical = service.SpotEngineConfig().default_json()
    else:
        partial = {"risk": {"max_positions": 5}}
        canonical = service.FuturesEngineConfig().default_json()

    with pytest.raises(ValueError, match="complete canonical document"):
        service._validate_config_candidate(config_type, partial)
    assert service._validate_config_candidate(config_type, canonical) == canonical
    tampered = deepcopy(canonical)
    tampered["unknown_runtime_override"] = True
    with pytest.raises(ValueError, match="without unknown keys"):
        service._validate_config_candidate(config_type, tampered)


def test_candidate_gate_vetoes_a_partial_spot_document_even_with_invariant_true():
    plan, policies, _profiles = _candidate_validation_fixture()
    spot_record = next(item for item in policies if item.config_type == "spot_engine")
    source = {
        "selling": {"never_sell_at_loss": True, "take_profit_pct": 1.5},
    }
    candidate = deepcopy(source)
    candidate["selling"]["take_profit_pct"] = 2.0
    spot_record.config_json = source
    plan.execution_payload = {
        "operation_type": "UPDATE_CONFIG_PROFILE",
        "config_profile_id": str(spot_record.id),
        "config_type": "spot_engine",
        "pool_id": None,
        "source_document": source,
        "candidate_document": candidate,
    }
    plan.proposed_diff = [{
        "op": "replace",
        "path": "/selling/take_profit_pct",
        "old_value": 1.5,
        "value": 2.0,
    }]

    result = service._candidate_validation_result(plan, policies, [])

    assert result["decision"] == "VETO"
    check = next(
        item for item in result["checks"]
        if item["check"] == "SPOT_CANDIDATE_SCHEMA_AND_INVARIANTS"
    )
    assert check["decision"] == "VETO"
    assert "complete canonical document" in check["reason"]


def test_candidate_gate_vetoes_a_partial_futures_document():
    plan, policies, _profiles = _candidate_validation_fixture()
    source = {"risk": {"max_positions": 5}}
    candidate = {"risk": {"max_positions": 6}}
    now = datetime(2026, 8, 13, tzinfo=timezone.utc)
    futures_record = SimpleNamespace(
        id=uuid.uuid4(),
        user_id=plan.user_id,
        pool_id=None,
        config_type="futures_engine",
        config_json=source,
        is_active=True,
        updated_at=now,
    )
    policies.append(futures_record)
    plan.execution_payload = {
        "operation_type": "UPDATE_CONFIG_PROFILE",
        "config_profile_id": str(futures_record.id),
        "config_type": "futures_engine",
        "pool_id": None,
        "source_document": source,
        "candidate_document": candidate,
    }
    plan.proposed_diff = [{
        "op": "replace",
        "path": "/risk/max_positions",
        "old_value": 5,
        "value": 6,
    }]

    result = service._candidate_validation_result(plan, policies, [])

    assert result["decision"] == "VETO"
    check = next(
        item for item in result["checks"]
        if item["check"] == "FUTURES_CANDIDATE_SCHEMA_AND_FIELD_BOUNDS"
    )
    assert check["decision"] == "VETO"


def test_plan_binding_hash_covers_execution_intent_but_not_validation_itself():
    plan, _policies, _profiles = _candidate_validation_fixture()
    original = service.plan_binding_hash(plan)

    plan.evidence["candidate_validation"] = {"decision": "PASS"}
    assert service.plan_binding_hash(plan) == original

    plan.execution_payload["profile_name"] = "tampered-target-name"
    assert service.plan_binding_hash(plan) != original


def test_candidate_aware_validator_vetoes_a_non_effective_policy_alias_target():
    plan, policies, _profiles = _candidate_validation_fixture()
    strategy_record = next(item for item in policies if item.config_type == "strategy")
    preferred_alias = SimpleNamespace(
        id=uuid.uuid4(),
        user_id=plan.user_id,
        pool_id=None,
        config_type="strategies",
        config_json={"execution": {"enabled": True}},
        is_active=True,
        updated_at=strategy_record.updated_at,
    )
    policies.insert(1, preferred_alias)
    plan.execution_payload = {
        "operation_type": "UPDATE_CONFIG_PROFILE",
        "config_profile_id": str(strategy_record.id),
        "config_type": "strategy",
        "pool_id": None,
        "source_document": {"execution": {"enabled": True}},
        "candidate_document": {"execution": {"enabled": False}},
    }
    plan.proposed_diff = [{
        "op": "replace",
        "path": "/execution/enabled",
        "old_value": True,
        "value": False,
        "reason": "evidence",
        "evidence_refs": [str(uuid.uuid4())],
    }]

    result = service._candidate_validation_result(plan, policies, [])

    assert result["decision"] == "VETO"
    target_check = next(
        item for item in result["checks"]
        if item["check"] == "TARGET_IS_EFFECTIVE_POLICY"
    )
    assert target_check["decision"] == "VETO"
    assert "not the effective global policy" in target_check["reason"]


@pytest.mark.asyncio
async def test_second_gate_validation_persists_idempotent_veto_audit(monkeypatch):
    plan, policies, profiles = _candidate_validation_fixture()
    db = _FakeDB(None)

    async def fake_get_plan(_db, user_id, plan_id, *, lock=False):
        assert user_id == plan.user_id
        assert plan_id == plan.id
        assert lock is False
        return plan

    async def fake_context(_db, user_id, loaded_plan):
        assert user_id == plan.user_id
        assert loaded_plan is plan
        return policies, profiles

    monkeypatch.setattr(service, "get_plan", fake_get_plan)
    monkeypatch.setattr(service, "_load_candidate_validation_context", fake_context)

    first = await service.validate_candidate_for_second_gate(
        db, plan.user_id, plan.id
    )
    second = await service.validate_candidate_for_second_gate(
        db, plan.user_id, plan.id
    )

    assert first == second
    assert first["decision"] == "VETO"
    assert db.commits == 0
    assert db.flushes == 1
    audits = [item for item in db.added if isinstance(item, CopilotAuditLog)]
    assert len(audits) == 1
    assert audits[0].event_type == "ANALYSIS_CHAT_CANDIDATE_VALIDATION_VETO"
    assert plan.evidence["candidate_validation"] == first


@pytest.mark.asyncio
@pytest.mark.asyncio
@pytest.mark.parametrize("dead_path", ["/weights", "/weights/signal", "/scoring/weights", "/scoring/weights/momentum"])
async def test_dry_run_rejects_dead_scoring_weights_before_loading_a_target(dead_path):
    # score_engine.py's docstring and robust_indicators/score.py's explicit
    # ``del weights`` confirm scoring.weights is dead configuration -- a
    # governed change touching it would look successful while having zero
    # effect on real scoring. FIX-AC-GOV-002 Fase 5.4.
    proposal = {
        "operation_type": "UPDATE_CONFIG_PROFILE",
        "target": {"config_type": "score"},
        "objective": "x",
        "risk": "x",
        "changes": [{"op": "replace", "path": dead_path, "value_json": "99", "evidence_refs": ["e1"]}],
    }
    with pytest.raises(ValueError, match="scoring.weights is dead configuration"):
        await service.create_dry_run(
            _FakeDB(None),
            uuid.uuid4(),
            proposal=proposal,
            conversation_id=uuid.uuid4(),
            message_id=uuid.uuid4(),
            evidence_ids={"e1"},
        )


@pytest.mark.asyncio
async def test_dry_run_does_not_reject_unrelated_score_paths_as_dead_weights():
    # Control case: a real, live-scoring field must not be caught by the
    # weights deny-list.
    proposal = {
        "operation_type": "UPDATE_CONFIG_PROFILE",
        "target": {"config_type": "score"},
        "objective": "x",
        "risk": "x",
        "changes": [{"op": "replace", "path": "/thresholds/buy", "value_json": "70", "evidence_refs": ["e1"]}],
    }
    with pytest.raises(LookupError, match="Configuration profile not found"):
        # _FakeDB(None) has no persisted score document, so the lookup that
        # runs *after* the weights deny-list check fails instead -- proving
        # the weights check itself did not fire for this unrelated path.
        await service.create_dry_run(
            _FakeDB(None),
            uuid.uuid4(),
            proposal=proposal,
            conversation_id=uuid.uuid4(),
            message_id=uuid.uuid4(),
            evidence_ids={"e1"},
        )


@pytest.mark.asyncio
async def test_dry_run_requires_evidence_on_every_individual_change():
    evidence_id = str(uuid.uuid4())
    proposal = {
        "operation_type": "UPDATE_PROFILE_CONFIG",
        "target": {"profile_id": str(uuid.uuid4())},
        "objective": "Adjust two evidenced profile parameters",
        "risk": "Operational change",
        "changes": [
            {
                "op": "replace",
                "path": "/default_timeframe",
                "value": "15m",
                "evidence_refs": [evidence_id],
            },
            {
                "op": "replace",
                "path": "/scoring/thresholds/buy",
                "value": 65,
                "evidence_refs": [],
            },
        ],
    }

    with pytest.raises(ValueError, match="Every proposed change requires evidence"):
        await service.create_dry_run(
            _FakeDB(None),
            uuid.uuid4(),
            proposal=proposal,
            conversation_id=uuid.uuid4(),
            message_id=uuid.uuid4(),
            evidence_ids={evidence_id},
        )


@pytest.mark.asyncio
async def test_dry_run_rejects_pool_scoped_config_before_loading_a_target():
    evidence_id = str(uuid.uuid4())
    proposal = {
        "operation_type": "UPDATE_CONFIG_PROFILE",
        "target": {
            "config_type": "score",
            "pool_id": str(uuid.uuid4()),
        },
        "objective": "Update a pool score",
        "risk": "Operational change",
        "changes": [{
            "op": "replace",
            "path": "/auto_select_top_n",
            "old_value": 5,
            "value": 6,
            "array_guards": [],
            "evidence_refs": [evidence_id],
        }],
    }

    with pytest.raises(ValueError, match="pool_id=null"):
        await service.create_dry_run(
            _FakeDB(None),
            uuid.uuid4(),
            proposal=proposal,
            conversation_id=uuid.uuid4(),
            message_id=uuid.uuid4(),
            evidence_ids={evidence_id},
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("config_type", ["risk", "strategy", "filters"])
async def test_dry_run_rejects_config_families_without_semantic_validator(config_type):
    evidence_id = str(uuid.uuid4())
    proposal = {
        "operation_type": "UPDATE_CONFIG_PROFILE",
        "target": {"config_type": config_type, "pool_id": None},
        "objective": "Unsupported policy mutation",
        "risk": "Operational change",
        "changes": [{
            "op": "replace",
            "path": "/enabled",
            "old_value": True,
            "value": False,
            "array_guards": [],
            "evidence_refs": [evidence_id],
        }],
    }

    with pytest.raises(ValueError, match="outside chat authority"):
        await service.create_dry_run(
            _FakeDB(None),
            uuid.uuid4(),
            proposal=proposal,
            conversation_id=uuid.uuid4(),
            message_id=uuid.uuid4(),
            evidence_ids={evidence_id},
        )


def test_dry_run_audit_collects_evidence_from_all_changes():
    source = __import__("inspect").getsource(service.create_dry_run)
    assert "referenced: set[str] = set()" in source
    assert "referenced.update(change_references)" in source


@pytest.mark.asyncio
async def test_human_confirmation_cannot_execute_profile_change_without_policy_semantics(monkeypatch):
    plan, policies, profiles = _candidate_validation_fixture()
    user_id = plan.user_id
    profile = profiles[0]
    profile.user_id = user_id
    profile.name = "Profile A"
    plan.execution_payload["profile_name"] = profile.name
    plan.target_state_hash = service.document_hash({
        "config": profile.config,
        "profile_version": profile.profile_version,
        "updated_at": profile.updated_at,
    })
    plan.evidence = {
        "evidence_ids": [str(uuid.uuid4())],
    }
    validation = service._candidate_validation_result(plan, policies, profiles)
    assert validation["decision"] == "VETO"
    plan.evidence["candidate_validation"] = validation
    plan.approved_at = None
    plan.approved_by = None
    plan.approval_text = None
    plan.executed_at = None
    plan.execution_result = None
    db = _FakeDB(profile)
    runtime = SimpleNamespace(
        id=uuid.uuid4(),
        user_id=user_id,
        pool_id=None,
        config_type="ai_analysis_chat_runtime",
        config_json={
            "enabled": True,
            "proposals_enabled": True,
            "governed_actions_enabled": True,
            "live_config_write_enabled": True,
        },
        is_active=True,
        updated_at=datetime(2026, 8, 13, tzinfo=timezone.utc),
    )

    async def _plan(_db, _user_id, _plan_id, *, lock=False):
        assert lock is True
        return plan

    async def _context(_db, _user_id, _plan):
        assert _plan is plan
        return policies, profiles

    async def _runtime(_db, _user_id):
        return runtime, service.AnalysisChatRuntimeConfig.model_validate(
            runtime.config_json
        )

    monkeypatch.setattr(service, "get_plan", _plan)
    monkeypatch.setattr(service, "_lock_candidate_execution_context", _context)
    monkeypatch.setattr(service, "_execution_runtime_record", _runtime)

    decision_id = str(uuid.uuid4())
    result = await service.approve_and_execute(
        db, user_id, plan.id, decision_id=decision_id
    )
    assert profile.config["default_timeframe"] == "5m"
    assert plan.status == "STALE"
    assert result["execution_result"]["live_config_changed"] is False
    assert result["execution_result"]["reason_code"] == (
        "ANALYSIS_CHAT_CANDIDATE_VALIDATION_REQUIRED"
    )
    assert result["execution_result"]["approval_decision_id"] == decision_id
    assert db.commits == 0
    assert db.flushes == 1
    assert not any(isinstance(item, ProfileAuditLog) for item in db.added)
    event_types = [
        item.event_type for item in db.added if isinstance(item, CopilotAuditLog)
    ]
    assert event_types == ["ANALYSIS_CHAT_CHANGE_EXECUTION_BLOCKED"]


@pytest.mark.asyncio
async def test_execution_fence_blocks_a_plan_without_persisted_candidate_pass(monkeypatch):
    plan, policies, profiles = _candidate_validation_fixture()
    profile = profiles[0]
    profile.name = "Profile A"
    plan.target_state_hash = service.document_hash({
        "config": profile.config,
        "profile_version": profile.profile_version,
        "updated_at": profile.updated_at,
    })
    plan.approved_at = None
    plan.approved_by = None
    plan.approval_text = None
    plan.executed_at = None
    plan.execution_result = None
    db = _FakeDB(profile)
    runtime = SimpleNamespace(
        id=uuid.uuid4(),
        pool_id=None,
        config_json={
            "enabled": True,
            "proposals_enabled": True,
            "governed_actions_enabled": True,
            "live_config_write_enabled": True,
        },
        updated_at=datetime(2026, 8, 13, tzinfo=timezone.utc),
    )

    async def _plan(*_args, **_kwargs):
        return plan

    async def _context(*_args, **_kwargs):
        return policies, profiles

    async def _runtime(*_args, **_kwargs):
        return runtime, service.AnalysisChatRuntimeConfig.model_validate(
            runtime.config_json
        )

    monkeypatch.setattr(service, "get_plan", _plan)
    monkeypatch.setattr(service, "_lock_candidate_execution_context", _context)
    monkeypatch.setattr(service, "_execution_runtime_record", _runtime)

    result = await service.approve_and_execute(
        db, plan.user_id, plan.id, decision_id=str(uuid.uuid4())
    )

    assert plan.status == "STALE"
    assert result["status"] == "STALE"
    assert result["execution_result"]["reason_code"] == (
        "ANALYSIS_CHAT_CANDIDATE_VALIDATION_REQUIRED"
    )
    assert plan.execution_result["live_config_changed"] is False
    assert profile.config["default_timeframe"] == "5m"
    assert db.commits == 0
    assert db.flushes == 1
    assert not any(isinstance(item, ProfileAuditLog) for item in db.added)
    audits = [item for item in db.added if isinstance(item, CopilotAuditLog)]
    assert [item.event_type for item in audits] == [
        "ANALYSIS_CHAT_CHANGE_EXECUTION_BLOCKED"
    ]


@pytest.mark.asyncio
async def test_execution_fence_marks_plan_stale_when_bound_intent_changes(monkeypatch):
    plan, policies, profiles = _candidate_validation_fixture()
    profile = profiles[0]
    profile.name = "Profile A"
    plan.target_state_hash = service.document_hash({
        "config": profile.config,
        "profile_version": profile.profile_version,
        "updated_at": profile.updated_at,
    })
    stored = service._candidate_validation_result(plan, policies, profiles)
    plan.evidence["candidate_validation"] = stored
    plan.objective = "A different objective after confirmation"
    plan.approved_at = None
    plan.approved_by = None
    plan.approval_text = None
    plan.executed_at = None
    plan.execution_result = None
    runtime = SimpleNamespace(
        id=uuid.uuid4(),
        pool_id=None,
        config_json={
            "enabled": True,
            "proposals_enabled": True,
            "governed_actions_enabled": True,
            "live_config_write_enabled": True,
        },
        updated_at=datetime(2026, 8, 13, tzinfo=timezone.utc),
    )
    db = _FakeDB(profile)

    async def _plan(*_args, **_kwargs):
        return plan

    async def _context(*_args, **_kwargs):
        return policies, profiles

    async def _runtime(*_args, **_kwargs):
        return runtime, service.AnalysisChatRuntimeConfig.model_validate(
            runtime.config_json
        )

    monkeypatch.setattr(service, "get_plan", _plan)
    monkeypatch.setattr(service, "_lock_candidate_execution_context", _context)
    monkeypatch.setattr(service, "_execution_runtime_record", _runtime)

    result = await service.approve_and_execute(
        db, plan.user_id, plan.id, decision_id=str(uuid.uuid4())
    )

    assert plan.status == "STALE"
    assert result["execution_result"]["reason_code"] == (
        "ANALYSIS_CHAT_CANDIDATE_VALIDATION_REQUIRED"
    )
    assert plan.execution_result["reason_code"] == (
        "ANALYSIS_CHAT_CANDIDATE_VALIDATION_REQUIRED"
    )
    assert plan.execution_result["stored_candidate_validation_hash"] != (
        plan.execution_result["current_candidate_validation_hash"]
    )
    assert profile.config["default_timeframe"] == "5m"
    assert not any(isinstance(item, ProfileAuditLog) for item in db.added)


@pytest.mark.asyncio
async def test_execution_fence_consumes_confirmation_when_runtime_writes_are_disabled(monkeypatch):
    plan, policies, profiles = _candidate_validation_fixture()
    profile = profiles[0]
    profile.name = "Profile A"
    plan.target_state_hash = service.document_hash({
        "config": profile.config,
        "profile_version": profile.profile_version,
        "updated_at": profile.updated_at,
    })
    plan.evidence["candidate_validation"] = service._candidate_validation_result(
        plan, policies, profiles
    )
    plan.approved_at = None
    plan.approved_by = None
    plan.approval_text = None
    plan.executed_at = None
    plan.execution_result = None
    runtime = SimpleNamespace(
        id=uuid.uuid4(),
        pool_id=None,
        config_json={
            "enabled": True,
            "proposals_enabled": True,
            "governed_actions_enabled": True,
            "live_config_write_enabled": False,
        },
        updated_at=datetime(2026, 8, 13, tzinfo=timezone.utc),
    )
    db = _FakeDB(runtime)

    async def _plan(*_args, **_kwargs):
        return plan

    async def _context(*_args, **_kwargs):
        return policies, profiles

    monkeypatch.setattr(service, "get_plan", _plan)
    monkeypatch.setattr(service, "_lock_candidate_execution_context", _context)

    result = await service.approve_and_execute(
        db, plan.user_id, plan.id, decision_id=str(uuid.uuid4())
    )

    assert plan.status == "STALE"
    assert result["execution_result"]["reason_code"] == (
        "ANALYSIS_CHAT_LIVE_CONFIG_WRITE_DISABLED"
    )
    assert plan.execution_result["live_config_changed"] is False
    assert profile.config["default_timeframe"] == "5m"
    assert [
        item.event_type for item in db.added if isinstance(item, CopilotAuditLog)
    ] == ["ANALYSIS_CHAT_CHANGE_EXECUTION_BLOCKED"]


@pytest.mark.asyncio
async def test_execution_fence_recomputes_the_exact_target_hash_before_approval(monkeypatch):
    plan, policies, profiles = _candidate_validation_fixture()
    profile = profiles[0]
    profile.name = "Profile A"
    plan.target_state_hash = service.document_hash({
        "config": {**profile.config, "default_timeframe": "1h"},
        "profile_version": profile.profile_version,
        "updated_at": profile.updated_at,
    })
    plan.evidence["candidate_validation"] = service._candidate_validation_result(
        plan, policies, profiles
    )
    plan.approved_at = None
    plan.approved_by = None
    plan.approval_text = None
    plan.executed_at = None
    plan.execution_result = None
    db = _FakeDB(profile)
    runtime = SimpleNamespace(
        id=uuid.uuid4(),
        pool_id=None,
        config_json={
            "enabled": True,
            "proposals_enabled": True,
            "governed_actions_enabled": True,
            "live_config_write_enabled": True,
        },
        updated_at=datetime(2026, 8, 13, tzinfo=timezone.utc),
    )

    async def _plan(*_args, **_kwargs):
        return plan

    async def _context(*_args, **_kwargs):
        return policies, profiles

    async def _runtime(*_args, **_kwargs):
        return runtime, service.AnalysisChatRuntimeConfig.model_validate(
            runtime.config_json
        )

    monkeypatch.setattr(service, "get_plan", _plan)
    monkeypatch.setattr(service, "_lock_candidate_execution_context", _context)
    monkeypatch.setattr(service, "_execution_runtime_record", _runtime)

    result = await service.approve_and_execute(
        db, plan.user_id, plan.id, decision_id=str(uuid.uuid4())
    )

    assert result["status"] == "STALE"
    assert result["execution_result"]["reason_code"] == (
        "ANALYSIS_CHAT_CANDIDATE_VALIDATION_REQUIRED"
    )
    assert profile.config["default_timeframe"] == "5m"
    assert not any(isinstance(item, ProfileAuditLog) for item in db.added)


@pytest.mark.asyncio
async def test_execution_fence_requires_a_canonical_decision_id_before_loading_plan():
    db = _FakeDB(None)
    with pytest.raises(
        service.GovernedExecutionFenceError,
        match="ANALYSIS_CHAT_GOVERNED_CHANGE_DECISION_REQUIRED",
    ):
        await service.approve_and_execute(
            db,
            uuid.uuid4(),
            uuid.uuid4(),
            decision_id=None,
        )
    assert db.commits == 0
    assert db.flushes == 0


@pytest.mark.asyncio
async def test_execution_fence_replay_requires_the_original_decision_id(monkeypatch):
    plan, _policies, _profiles = _candidate_validation_fixture()
    original_decision_id = str(uuid.uuid4())
    plan.status = "EXECUTED"
    plan.approved_at = datetime(2026, 8, 13, tzinfo=timezone.utc)
    plan.executed_at = plan.approved_at
    plan.execution_result = {
        "status": "EXECUTED",
        "approval_decision_id": original_decision_id,
        "live_config_changed": True,
    }
    db = _FakeDB(None)

    async def _plan(*_args, **_kwargs):
        return plan

    monkeypatch.setattr(service, "get_plan", _plan)

    replay = await service.approve_and_execute(
        db, plan.user_id, plan.id, decision_id=original_decision_id
    )
    assert replay["execution_result"] == plan.execution_result
    assert db.commits == 0

    with pytest.raises(
        service.GovernedExecutionFenceError,
        match="ANALYSIS_CHAT_GOVERNED_CHANGE_DECISION_CONFLICT",
    ):
        await service.approve_and_execute(
            db, plan.user_id, plan.id, decision_id=str(uuid.uuid4())
        )

    plan.status = "STALE"
    plan.execution_result = {
        "status": "BLOCKED",
        "reason_code": "ANALYSIS_CHAT_CANDIDATE_VALIDATION_STALE",
        "approval_decision_id": original_decision_id,
        "live_config_changed": False,
    }
    blocked_replay = await service.approve_and_execute(
        db, plan.user_id, plan.id, decision_id=original_decision_id
    )
    assert blocked_replay["status"] == "STALE"
    assert blocked_replay["execution_result"]["status"] == "BLOCKED"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("cache_fails", "expected_status", "expected_event"),
    [
        (False, "COMPLETED", "ANALYSIS_CHAT_CACHE_INVALIDATION_COMPLETED"),
        (
            True,
            "RECONCILIATION_REQUIRED",
            "ANALYSIS_CHAT_CACHE_INVALIDATION_RECONCILIATION_REQUIRED",
        ),
    ],
)
async def test_cache_reconciliation_runs_in_a_separate_transaction_without_reclassifying_write(
    monkeypatch,
    cache_fails,
    expected_status,
    expected_event,
):
    user_id = uuid.uuid4()
    decision_id = str(uuid.uuid4())
    plan = SimpleNamespace(
        id=uuid.uuid4(),
        user_id=user_id,
        target_type="CONFIG_PROFILE",
        target_id=str(uuid.uuid4()),
        objective="Update score policy",
        risk_assessment="Audited policy change",
        proposed_diff=[],
        approved_at=datetime(2026, 8, 13, tzinfo=timezone.utc),
        executed_at=datetime(2026, 8, 13, tzinfo=timezone.utc),
        status="EXECUTED",
        execution_payload={
            "operation_type": "UPDATE_CONFIG_PROFILE",
            "config_type": "score",
            "pool_id": None,
        },
        execution_result={
            "status": "EXECUTED",
            "resource_type": "CONFIG_PROFILE",
            "resource_id": str(uuid.uuid4()),
            "config_type": "score",
            "approval_decision_id": decision_id,
            "live_config_changed": True,
            "cache_invalidation_status": "PENDING_AFTER_COMMIT",
        },
    )
    db = _FakeDB(None)

    async def _plan(*_args, **kwargs):
        assert kwargs["lock"] is True
        return plan

    class _Redis:
        def __init__(self):
            self.deleted = []

        async def delete(self, cache_key):
            self.deleted.append(cache_key)
            if cache_fails:
                raise RuntimeError("redis unavailable")
            return 1

    redis = _Redis()
    monkeypatch.setattr(service, "get_plan", _plan)
    # Exercise ConfigService.invalidate_cache itself.  Only its Redis
    # dependency is replaced so this catches error-suppression regressions.
    monkeypatch.setattr(
        service.config_service,
        "_strict_redis_factory",
        lambda: redis,
    )

    result = await service.reconcile_execution_cache(
        db,
        user_id,
        plan.id,
        decision_id=decision_id,
    )

    assert result["execution_result"]["live_config_changed"] is True
    assert result["execution_result"]["cache_invalidation_status"] == expected_status
    assert plan.status == "EXECUTED"
    assert db.commits == 0
    assert db.flushes == 1
    assert redis.deleted == [f"config:{user_id}:global:score"]
    audits = [item for item in db.added if isinstance(item, CopilotAuditLog)]
    assert [item.event_type for item in audits] == [expected_event]


@pytest.mark.asyncio
async def test_cache_reconciliation_requires_proof_when_redis_client_is_unavailable(
    monkeypatch,
):
    user_id = uuid.uuid4()
    decision_id = str(uuid.uuid4())
    plan = SimpleNamespace(
        id=uuid.uuid4(),
        user_id=user_id,
        target_type="CONFIG_PROFILE",
        target_id=str(uuid.uuid4()),
        objective="Update score policy",
        risk_assessment="Audited policy change",
        proposed_diff=[],
        approved_at=datetime(2026, 8, 13, tzinfo=timezone.utc),
        executed_at=datetime(2026, 8, 13, tzinfo=timezone.utc),
        status="EXECUTED",
        execution_payload={
            "operation_type": "UPDATE_CONFIG_PROFILE",
            "config_type": "score",
            "pool_id": None,
        },
        execution_result={
            "status": "EXECUTED",
            "resource_type": "CONFIG_PROFILE",
            "resource_id": str(uuid.uuid4()),
            "config_type": "score",
            "approval_decision_id": decision_id,
            "live_config_changed": True,
            "cache_invalidation_status": "PENDING_AFTER_COMMIT",
        },
    )
    db = _FakeDB(None)

    async def _plan(*_args, **kwargs):
        assert kwargs["lock"] is True
        return plan

    monkeypatch.setattr(service, "get_plan", _plan)
    monkeypatch.setattr(
        service.config_service,
        "_strict_redis_factory",
        lambda: None,
    )

    result = await service.reconcile_execution_cache(
        db,
        user_id,
        plan.id,
        decision_id=decision_id,
    )

    assert (
        result["execution_result"]["cache_invalidation_status"]
        == "RECONCILIATION_REQUIRED"
    )
    audits = [item for item in db.added if isinstance(item, CopilotAuditLog)]
    assert [item.event_type for item in audits] == [
        "ANALYSIS_CHAT_CACHE_INVALIDATION_RECONCILIATION_REQUIRED"
    ]


@pytest.mark.asyncio
async def test_cache_reconciliation_backoff_is_durable_bounded_and_idempotent(
    monkeypatch,
):
    user_id = uuid.uuid4()
    decision_id = str(uuid.uuid4())
    current_time = [datetime(2026, 8, 13, 12, tzinfo=timezone.utc)]
    plan = SimpleNamespace(
        id=uuid.uuid4(),
        user_id=user_id,
        target_type="CONFIG_PROFILE",
        target_id=str(uuid.uuid4()),
        objective="Update score policy",
        risk_assessment="Audited policy change",
        proposed_diff=[],
        approved_at=current_time[0],
        executed_at=current_time[0],
        status="EXECUTED",
        execution_payload={
            "operation_type": "UPDATE_CONFIG_PROFILE",
            "config_type": "score",
            "pool_id": None,
        },
        execution_result={
            "status": "EXECUTED",
            "resource_type": "CONFIG_PROFILE",
            "resource_id": str(uuid.uuid4()),
            "config_type": "score",
            "approval_decision_id": decision_id,
            "live_config_changed": True,
            **service._pending_cache_reconciliation(current_time[0]),
        },
    )
    db = _FakeDB(None)

    async def _plan(*_args, **_kwargs):
        return plan

    class _Redis:
        def __init__(self):
            self.delete_count = 0

        async def delete(self, _cache_key):
            self.delete_count += 1
            raise RuntimeError("redis unavailable")

    redis = _Redis()
    monkeypatch.setattr(service, "get_plan", _plan)
    monkeypatch.setattr(service, "_now", lambda: current_time[0])
    monkeypatch.setattr(
        service.config_service,
        "_strict_redis_factory",
        lambda: redis,
    )

    for expected_attempt in range(1, service.CACHE_RECONCILIATION_MAX_ATTEMPTS + 1):
        result = await service.reconcile_execution_cache(
            db,
            user_id,
            plan.id,
            decision_id=decision_id,
        )
        cache = result["execution_result"]
        assert cache["cache_reconciliation_attempts"] == expected_attempt
        assert cache["live_config_changed"] is True
        assert plan.status == "EXECUTED"
        next_retry_at = cache["cache_reconciliation_next_retry_at"]
        if next_retry_at:
            current_time[0] = datetime.fromisoformat(next_retry_at)

    assert cache["cache_invalidation_status"] == "RECONCILIATION_REQUIRED"
    assert cache["cache_reconciliation_retry_state"] == "EXHAUSTED"
    assert cache["cache_reconciliation_next_retry_at"] is None
    assert redis.delete_count == service.CACHE_RECONCILIATION_MAX_ATTEMPTS

    current_time[0] += timedelta(days=1)
    replay = await service.reconcile_execution_cache(
        db,
        user_id,
        plan.id,
        decision_id=decision_id,
    )
    assert replay["execution_result"]["cache_reconciliation_retry_state"] == "EXHAUSTED"
    assert redis.delete_count == service.CACHE_RECONCILIATION_MAX_ATTEMPTS


@pytest.mark.asyncio
async def test_cache_reconciliation_outbox_claim_has_a_durable_dispatch_lease():
    now = datetime(2026, 8, 13, 12, tzinfo=timezone.utc)
    plan = SimpleNamespace(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        action_type=service.ACTION_TYPE,
        status="EXECUTED",
        executed_at=now,
        execution_result={
            "status": "EXECUTED",
            "resource_type": "CONFIG_PROFILE",
            "resource_id": str(uuid.uuid4()),
            "config_type": "score",
            **service._pending_cache_reconciliation(now),
        },
    )
    first_db = _FakeDB(plan)

    first = await service.claim_due_cache_reconciliations(first_db, now=now)

    assert first == [{
        "user_id": str(plan.user_id),
        "plan_id": str(plan.id),
        "kind": "EXECUTION",
    }]
    assert (
        plan.execution_result["cache_reconciliation_retry_state"]
        == "DISPATCHED"
    )
    assert plan.execution_result["cache_reconciliation_dispatch_lease_until"]

    duplicate_db = _FakeDB(plan)
    duplicate = await service.claim_due_cache_reconciliations(
        duplicate_db,
        now=now + timedelta(seconds=1),
    )
    assert duplicate == []

    recovered_db = _FakeDB(plan)
    recovered = await service.claim_due_cache_reconciliations(
        recovered_db,
        now=now + timedelta(seconds=121),
    )
    assert recovered[0]["plan_id"] == str(plan.id)


@pytest.mark.asyncio
async def test_cache_reconciliation_outbox_excludes_exhausted_before_limit():
    from sqlalchemy.dialects import postgresql

    class _CaptureDB:
        statement = None

        async def execute(self, statement):
            self.statement = statement
            return _ScalarResult(None)

        async def flush(self):
            return None

    db = _CaptureDB()
    assert await service.claim_due_cache_reconciliations(db, limit=50) == []

    sql = str(db.statement.compile(
        dialect=postgresql.dialect(),
        compile_kwargs={"literal_binds": True},
    ))
    assert sql.count("cache_reconciliation_retry_state") >= 2
    assert sql.count("EXHAUSTED") == 2
    assert sql.rfind("EXHAUSTED") < sql.index("LIMIT")


@pytest.mark.asyncio
async def test_config_cache_invalidation_keeps_legacy_fail_open_default(monkeypatch):
    class _Redis:
        async def delete(self, _cache_key):
            raise RuntimeError("redis unavailable")

    monkeypatch.setattr(service.config_service, "redis", _Redis())
    monkeypatch.setattr(
        service.config_service,
        "_strict_redis_factory",
        _Redis,
    )

    assert (
        await service.config_service.invalidate_cache("score", uuid.uuid4())
        is False
    )
    with pytest.raises(RuntimeError, match="redis unavailable"):
        await service.config_service.invalidate_cache(
            "score",
            uuid.uuid4(),
            strict=True,
        )


def test_strict_config_cache_uses_fresh_client_in_each_asyncio_run(monkeypatch):
    clients = []

    class _LoopBoundRedis:
        def __init__(self):
            self.delete_loop = None
            self.close_loop = None

        async def delete(self, _cache_key):
            self.delete_loop = asyncio.get_running_loop()
            return 1

        async def aclose(self):
            self.close_loop = asyncio.get_running_loop()

    def _factory():
        client = _LoopBoundRedis()
        clients.append(client)
        return client

    monkeypatch.setattr(
        service.config_service,
        "_strict_redis_factory",
        _factory,
    )

    user_id = uuid.uuid4()
    assert asyncio.run(service.config_service.invalidate_cache(
        "score", user_id, strict=True,
    )) is True
    assert asyncio.run(service.config_service.invalidate_cache(
        "score", user_id, strict=True,
    )) is True

    assert len(clients) == 2
    assert clients[0] is not clients[1]
    assert clients[0].delete_loop is clients[0].close_loop
    assert clients[1].delete_loop is clients[1].close_loop
    assert clients[0].delete_loop is not clients[1].delete_loop


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("cache_fails", "expected_status", "expected_event"),
    [
        (False, "COMPLETED", "ANALYSIS_CHAT_ROLLBACK_CACHE_INVALIDATION_COMPLETED"),
        (
            True,
            "RECONCILIATION_REQUIRED",
            "ANALYSIS_CHAT_ROLLBACK_CACHE_INVALIDATION_RECONCILIATION_REQUIRED",
        ),
    ],
)
async def test_rollback_cache_reconciliation_uses_strict_real_invalidation(
    monkeypatch,
    cache_fails,
    expected_status,
    expected_event,
):
    user_id = uuid.uuid4()
    plan = SimpleNamespace(
        id=uuid.uuid4(),
        user_id=user_id,
        target_type="CONFIG_PROFILE",
        target_id=str(uuid.uuid4()),
        objective="Restore score policy",
        risk_assessment="Restore audited snapshot",
        proposed_diff=[],
        approved_at=datetime(2026, 8, 13, tzinfo=timezone.utc),
        executed_at=datetime(2026, 8, 13, tzinfo=timezone.utc),
        status="ROLLED_BACK",
        execution_payload={
            "operation_type": "UPDATE_CONFIG_PROFILE",
            "config_type": "score",
            "pool_id": None,
        },
        execution_result={
            "status": "EXECUTED",
            "live_config_changed": True,
            "rollback": {
                "status": "ROLLED_BACK",
                "resource_type": "CONFIG_PROFILE",
                "resource_id": str(uuid.uuid4()),
                "config_type": "score",
                "pool_id": None,
                "cache_invalidation_status": "PENDING_AFTER_COMMIT",
            },
        },
    )
    db = _FakeDB(None)

    async def _plan(*_args, **kwargs):
        assert kwargs["lock"] is True
        return plan

    class _Redis:
        async def delete(self, _cache_key):
            if cache_fails:
                raise RuntimeError("redis unavailable")
            return 1

    monkeypatch.setattr(service, "get_plan", _plan)
    monkeypatch.setattr(
        service.config_service,
        "_strict_redis_factory",
        _Redis,
    )

    result = await service.reconcile_rollback_cache(db, user_id, plan.id)

    assert (
        result["execution_result"]["rollback"]["cache_invalidation_status"]
        == expected_status
    )
    assert plan.status == "ROLLED_BACK"
    assert db.commits == 0
    assert db.flushes == 1
    audits = [item for item in db.added if isinstance(item, CopilotAuditLog)]
    assert [item.event_type for item in audits] == [expected_event]


@pytest.mark.asyncio
async def test_config_rollback_commits_no_external_side_effect_and_leaves_pending_marker(
    monkeypatch,
):
    user_id = uuid.uuid4()
    resource_id = uuid.uuid4()
    source = _score_document(points=20)
    current = _score_document(points=25)
    resource = SimpleNamespace(
        id=resource_id,
        user_id=user_id,
        pool_id=None,
        config_type="score",
        config_json=current,
        is_active=True,
        updated_at=datetime(2026, 8, 13, tzinfo=timezone.utc),
    )
    plan = SimpleNamespace(
        id=uuid.uuid4(),
        user_id=user_id,
        target_type="CONFIG_PROFILE",
        target_id=str(resource_id),
        objective="Restore score policy",
        risk_assessment="Restore audited snapshot",
        proposed_diff=[],
        approved_at=datetime(2026, 8, 13, tzinfo=timezone.utc),
        executed_at=datetime(2026, 8, 13, tzinfo=timezone.utc),
        status="EXECUTED",
        execution_payload={
            "operation_type": "UPDATE_CONFIG_PROFILE",
            "config_profile_id": str(resource_id),
            "config_type": "score",
            "pool_id": None,
        },
        execution_result={
            "status": "EXECUTED",
            "resource_type": "CONFIG_PROFILE",
            "resource_id": str(resource_id),
            "config_type": "score",
            "new_document_hash": service.document_hash(current),
            "live_config_changed": True,
            "cache_invalidation_status": "COMPLETED",
        },
        rollback_plan={"source_document": source},
    )
    db = _FakeDB(resource)

    async def _plan(*_args, **kwargs):
        assert kwargs["lock"] is True
        return plan

    monkeypatch.setattr(service, "get_plan", _plan)

    result = await service.rollback(
        db,
        user_id,
        plan.id,
        confirmation_text=service.ROLLBACK_TEXT,
    )

    assert resource.config_json == source
    assert plan.status == "ROLLED_BACK"
    rollback_result = result["execution_result"]["rollback"]
    assert rollback_result["status"] == "ROLLED_BACK"
    assert rollback_result["resource_id"] == str(resource_id)
    assert rollback_result["restored_document_hash"] == service.document_hash(source)
    assert rollback_result["resource_type"] == "CONFIG_PROFILE"
    assert rollback_result["config_type"] == "score"
    assert rollback_result["pool_id"] is None
    assert rollback_result["cache_invalidation_status"] == "PENDING_AFTER_COMMIT"
    assert rollback_result["cache_reconciliation_attempts"] == 0
    assert rollback_result["cache_reconciliation_retry_state"] == "PENDING"
    assert db.commits == 0
    assert db.flushes == 1


@pytest.mark.asyncio
async def test_execution_runtime_record_rejects_unknown_schema_fields():
    runtime = SimpleNamespace(
        id=uuid.uuid4(),
        config_json={
            "enabled": True,
            "proposals_enabled": True,
            "governed_actions_enabled": True,
            "live_config_write_enabled": True,
            "unregistered_write_bypass": True,
        },
    )

    with pytest.raises(
        service.GovernedExecutionFenceError,
        match="ANALYSIS_CHAT_RUNTIME_CONFIG_INVALID",
    ):
        await service._execution_runtime_record(_FakeDB(runtime), uuid.uuid4())


def test_execution_leaves_cache_reconciliation_after_the_outer_commit():
    source = inspect.getsource(service.approve_and_execute)

    assert "await db.commit()" not in source
    assert "await config_service.invalidate_cache" not in source
    assert "result.update(_pending_cache_reconciliation(now))" in source
    assert "await db.flush()" in source


def test_rollback_leaves_cache_reconciliation_after_the_outer_commit():
    rollback_source = inspect.getsource(service.rollback)
    assert "await db.commit()" not in rollback_source
    assert "await config_service.invalidate_cache" not in rollback_source
    assert "**_pending_cache_reconciliation(now)" in rollback_source
    assert "await db.flush()" in rollback_source

    from app.api.analysis_chat import rollback_proposal

    route_source = inspect.getsource(rollback_proposal)
    rollback_position = route_source.index("rollback_governed_change(")
    commit_position = route_source.index("await db.commit()")
    reconciliation_position = route_source.index("reconcile_rollback_cache(")
    assert rollback_position < commit_position < reconciliation_position


def test_graph_owned_governed_paths_never_close_the_outer_transaction():
    for operation in (
        service.create_dry_run,
        service.validate_candidate_for_second_gate,
        service.approve_and_execute,
    ):
        source = inspect.getsource(operation)
        assert "await db.commit()" not in source
        assert "await db.refresh(" not in source
        assert "await db.flush()" in source


def test_execution_context_uses_table_and_deterministic_row_locks():
    source = inspect.getsource(service._lock_candidate_execution_context)
    assert "LOCK TABLE config_profiles, profiles IN SHARE ROW EXCLUSIVE MODE" in source
    assert source.count("with_for_update()") == 2
    assert "ConfigProfile.config_type," in source
    assert "profile_query.order_by(Profile.id)" in source


def test_graph_handler_reconciles_cache_only_after_node_transaction_and_labels_blocked():
    from app.ai_orchestration.langgraph.analysis_chat_handler import (
        AnalysisChatGraphNodeHandler,
    )

    handle_source = inspect.getsource(AnalysisChatGraphNodeHandler.handle)
    committed_position = handle_source.index("updates = await self._transaction(_handle)")
    reconciliation_position = handle_source.index("reconcile_execution_cache(")
    assert committed_position < reconciliation_position

    node_source = inspect.getsource(AnalysisChatGraphNodeHandler._node_updates)
    blocked_position = node_source.index(
        'if execution_result.get("status") == "BLOCKED":'
    )
    applied_position = node_source.index(
        '"Alteração confirmada e aplicada com registro de auditoria'
    )
    assert blocked_position < applied_position
    assert "Nenhuma configuração operacional foi alterada" in node_source
