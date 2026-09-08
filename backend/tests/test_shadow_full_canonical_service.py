from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
import uuid

import pytest

from app.ai_orchestration.hashing import canonical_hash, canonical_json
from app.models.shadow_trade import ShadowTrade
from app.models.systemic_ai import AIAnalysisShardRecord, AIDatasetSnapshotItemRecord
from app.services.shadow_full_canonical_service import (
    CONTRACT_VERSION,
    PROVIDER_PROJECTION_CONTRACT_VERSION,
    PRICE_POSITION_INDICATORS,
    CanonicalItem,
    ShadowCanonicalContractError,
    canonical_trade_payload,
    plan_shards,
    provider_shard_payload,
    reconcile_shard_results,
)


def _features(seed: float) -> dict:
    return {name: seed + index / 100 for index, name in enumerate(PRICE_POSITION_INDICATORS)}


def _trade() -> ShadowTrade:
    now = datetime.now(timezone.utc)
    return ShadowTrade(
        id=uuid.uuid4(), user_id=uuid.uuid4(), symbol="XRP_USDT", amount_usdt=1000,
        entry_price=0.59, entry_timestamp=now, exit_price=0.60, exit_timestamp=now,
        outcome="TP_HIT", status="COMPLETED",
        config_snapshot={"final_score": 80},
        features_snapshot=_features(1.0), features_snapshot_exit=_features(2.0),
        exit_metrics_json={"pnl_pct": 1.2}, rules_snapshot={"entry_triggers": []},
        entry_risk_features_json={
            "contract_status": {
                "status": "VALID",
                "entry_risk_contract_valid": True,
                "reason_codes": [],
            }
        },
        orchestrator_payload={"source": "test"}, reason_codes=[],
        feature_source_times={"ema21_distance_pct": now.isoformat()},
        event_id=uuid.uuid4(), snapshot_id=uuid.uuid4(), profile_id=uuid.uuid4(),
        profile_version_id=uuid.uuid4(), score_engine_version_id=uuid.uuid4(),
        exchange="gateio", timeframe="5m", feature_schema_version="features-v1",
        feature_extractor_version="extractor-v1", capture_contract_version="capture-v1",
        label_contract_version="label-v1", barrier_contract_version="barrier-v1",
        feature_source_at=now, features_captured_at=now, feature_hash="a" * 64,
        profile_config_hash="b" * 64, score_engine_config_hash="c" * 64,
        lineage_status="CANONICAL", watchlist_id=uuid.uuid4(), watchlist_name="L3",
        watchlist_level="L3", lineage_confidence="HIGH", lineage_source="NATIVE",
        lineage_resolved_at=now, entry_risk_capture_status="VALID",
        entry_risk_captured_at=now, label_resolved_at=now,
    )


def _item(
    position: int,
    padding: int = 0,
    contract_version: str = "shadow-portfolio-full-canonical-v1",
) -> CanonicalItem:
    payload = {
        "input_contract_version": contract_version,
        "report_position": position,
        "trade": {"id": str(uuid.uuid4()), "padding": "x" * padding},
    }
    encoded = str(payload).encode()
    return CanonicalItem(
        record_id=uuid.uuid4(), shadow_trade_id=uuid.UUID(payload["trade"]["id"]),
        report_position=position, payload=payload, item_hash=canonical_hash(payload),
        payload_bytes=len(encoded), estimated_tokens=max(1, len(encoded) // 3),
    )


def test_complete_payload_keeps_all_entry_exit_indicators_and_risk_components():
    payload = canonical_trade_payload(uuid.uuid4(), 0, _trade())
    assert payload["input_contract_version"] == CONTRACT_VERSION
    for indicator in PRICE_POSITION_INDICATORS:
        assert indicator in payload["snapshots"]["entry_features"]
        assert indicator in payload["snapshots"]["exit_features"]
    assert payload["snapshots"]["entry_features"]["ema21_distance_pct"] is not None
    assert payload["snapshots"]["entry_risk"]["contract_status"]["entry_risk_contract_valid"] is True
    assert set(payload["virtual_indicators"]["entry"]["breakout_distance_pct"]) == {"5m", "15m", "30m", "1h"}


def test_missing_historical_entry_indicator_is_explicit_without_fabrication():
    trade = _trade()
    trade.features_snapshot = {**trade.features_snapshot, "ema21_distance_pct": None}
    payload = canonical_trade_payload(uuid.uuid4(), 0, trade)

    assert payload["evidence_availability"]["entry_feature_indicators"]["status"] == "PARTIAL"
    assert payload["null_reasons"]["snapshots.entry_features.ema21_distance_pct"] == (
        "HISTORICAL_ENTRY_INDICATOR_UNAVAILABLE"
    )


def test_missing_historical_exit_capture_is_explicit_but_does_not_block_entry_audit():
    trade = _trade()
    trade.features_snapshot_exit = None
    trade.exit_metrics_json = None

    payload = canonical_trade_payload(uuid.uuid4(), 0, trade)

    assert payload["evidence_availability"]["exit_features"] == {
        "status": "UNAVAILABLE",
        "reason_codes": ["HISTORICAL_EXIT_CAPTURE_UNAVAILABLE"],
    }
    assert payload["evidence_availability"]["exit_metrics"] == {
        "status": "UNAVAILABLE",
        "reason_codes": ["HISTORICAL_EXIT_CAPTURE_UNAVAILABLE"],
    }
    assert payload["evidence_availability"]["exit_feature_indicators"]["status"] == "PARTIAL"
    assert payload["null_reasons"]["snapshots.exit_features"] == (
        "HISTORICAL_EXIT_CAPTURE_UNAVAILABLE"
    )
    assert payload["null_reasons"]["snapshots.exit_metrics"] == (
        "HISTORICAL_EXIT_CAPTURE_UNAVAILABLE"
    )


def test_missing_historical_temporal_lineage_is_explicit_without_fabrication():
    trade = _trade()
    trade.feature_source_at = None
    trade.feature_source_times = {}

    payload = canonical_trade_payload(uuid.uuid4(), 0, trade)

    assert payload["trade"]["feature_source_at"] is None
    assert payload["snapshots"]["feature_source_times"] == {}
    assert payload["evidence_availability"]["entry_temporal_lineage"] == {
        "status": "UNAVAILABLE",
        "reason_codes": ["HISTORICAL_FEATURE_SOURCE_TIMESTAMP_UNAVAILABLE"],
        "unavailable_paths": [
            "trade.feature_source_at",
            "snapshots.feature_source_times",
        ],
    }
    assert payload["null_reasons"]["trade.feature_source_at"] == (
        "HISTORICAL_FEATURE_SOURCE_TIMESTAMP_UNAVAILABLE"
    )


def test_missing_historical_risk_metadata_is_explicit_without_blocking_report():
    trade = _trade()
    trade.entry_risk_captured_at = None
    trade.entry_risk_features_json = {
        "contract_status": {
            "entry_risk_contract_valid": True,
            "reason_codes": [],
        }
    }

    payload = canonical_trade_payload(uuid.uuid4(), 0, trade)

    assert payload["evidence_availability"]["entry_risk_contract"] == {
        "status": "PARTIAL",
        "reason_codes": ["HISTORICAL_ENTRY_RISK_METADATA_UNAVAILABLE"],
        "unavailable_paths": [
            "trade.entry_risk_captured_at",
            "snapshots.entry_risk.contract_status.status",
        ],
    }
    assert payload["null_reasons"]["trade.entry_risk_captured_at"] == (
        "HISTORICAL_ENTRY_RISK_METADATA_UNAVAILABLE"
    )


def test_reconstructible_partial_risk_snapshot_with_reasons_is_complete_evidence():
    trade = _trade()
    trade.entry_risk_capture_status = "PARTIAL"
    trade.entry_risk_features_json = {
        "contract_status": {
            "status": "PARTIAL",
            "entry_risk_contract_valid": False,
            "reconstructible": True,
            "reason_codes": ["LEGACY_SNAPSHOT_MISMATCH"],
        }
    }

    payload = canonical_trade_payload(uuid.uuid4(), 0, trade)

    assert payload["snapshots"]["entry_risk"]["contract_status"] == (
        trade.entry_risk_features_json["contract_status"]
    )


@pytest.mark.parametrize("status", ["PENDING", "ERROR", "INVALID", "NOT_AVAILABLE"])
def test_non_terminal_risk_snapshot_is_preserved_as_unavailable_evidence(status: str):
    trade = _trade()
    trade.entry_risk_capture_status = status
    trade.entry_risk_features_json = {
        "contract_status": {
            "status": status,
            "entry_risk_contract_valid": False,
            "reconstructible": False,
            "reason_codes": [f"{status}_RISK_CAPTURE"],
        }
    }

    payload = canonical_trade_payload(uuid.uuid4(), 0, trade)

    assert payload["snapshots"]["entry_risk"]["contract_status"]["status"] == status
    assert payload["evidence_availability"]["entry_risk_contract"]["status"] == "UNAVAILABLE"
    assert payload["evidence_availability"]["entry_risk_contract"]["reason_codes"] == [
        "ENTRY_RISK_CAPTURE_NOT_TERMINAL"
    ]


def test_contradictory_entry_risk_status_still_blocks_capture():
    trade = _trade()
    trade.entry_risk_capture_status = "VALID"
    trade.entry_risk_features_json = {
        "contract_status": {
            "status": "PENDING",
            "entry_risk_contract_valid": False,
            "reason_codes": ["PENDING_RISK_CAPTURE"],
        }
    }

    with pytest.raises(ShadowCanonicalContractError) as exc_info:
        canonical_trade_payload(uuid.uuid4(), 0, trade)

    assert "trade.entry_risk_capture_status" in exc_info.value.details["paths"]


@pytest.mark.parametrize(
    ("reconstructible", "reason_codes", "missing_path"),
    [
        (False, ["MISSING_COMPONENT"], "snapshots.entry_risk.contract_status.reconstructible"),
        (True, [], "snapshots.entry_risk.contract_status.reason_codes"),
    ],
)
def test_incomplete_partial_risk_snapshot_blocks_capture(
    reconstructible: bool,
    reason_codes: list[str],
    missing_path: str,
):
    trade = _trade()
    trade.entry_risk_capture_status = "PARTIAL"
    trade.entry_risk_features_json = {
        "contract_status": {
            "status": "PARTIAL",
            "entry_risk_contract_valid": False,
            "reconstructible": reconstructible,
            "reason_codes": reason_codes,
        }
    }

    with pytest.raises(ShadowCanonicalContractError) as exc_info:
        canonical_trade_payload(uuid.uuid4(), 0, trade)

    assert missing_path in exc_info.value.details["paths"]


def test_capture_only_status_migration_extends_single_canonical_head():
    migration = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "200_ai_graph_run_captured_status.py"
    ).read_text(encoding="utf-8")

    assert 'revision = "200_ai_graph_run_captured"' in migration
    assert 'down_revision = "199_shadow_full_canonical"' in migration
    assert "'FAILED','CANCELLED','CAPTURED'" in migration


def test_safety_net_lineage_repair_is_exact_audited_and_reversible():
    migration = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "201_shadow_safety_net_lineage.py"
    ).read_text(encoding="utf-8")

    assert 'revision = "201_shadow_safety_net_lineage"' in migration
    assert 'down_revision = "200_ai_graph_run_captured"' in migration
    assert "shadow_canonical_lineage_repair_audit" in migration
    assert "single_candidate_safety_net" in migration
    assert "pv.config_hash = st.profile_config_hash" in migration
    assert "HAVING count(*) = 1" in migration
    assert "JOIN_PROFILE_UNIQUE" in migration
    assert "prior_values" in migration
    assert "def downgrade()" in migration


def test_historical_lineage_repair_does_not_depend_on_current_profile_status():
    migration = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "223_shadow_l3_historical_lineage.py"
    ).read_text(encoding="utf-8")

    assert 'down_revision = "222_shadow_l3_lineage_retry"' in migration
    assert "pv.config_hash = st.profile_config_hash" in migration
    assert "HAVING count(*) = 1" in migration
    assert "p.is_active" not in migration
    assert "DROP TABLE shadow_canonical_lineage_repair_audit" not in migration
    assert "DELETE FROM shadow_canonical_lineage_repair_audit" in migration


def test_anthropic_canonical_shard_schema_has_no_free_form_objects():
    from app.ai_orchestration.provider_adapters import anthropic_output_config
    from app.services.systemic_langgraph_bridge import _SHADOW_SHARD_OUTPUT_SCHEMA

    schema = anthropic_output_config(_SHADOW_SHARD_OUTPUT_SCHEMA)["format"]["schema"]

    def assert_explicit_objects(node: object) -> None:
        if isinstance(node, dict):
            if node.get("type") == "object":
                assert isinstance(node.get("properties"), dict)
                assert node["properties"]
                assert node.get("additionalProperties") is False
            for value in node.values():
                assert_explicit_objects(value)
        elif isinstance(node, list):
            for value in node:
                assert_explicit_objects(value)

    assert_explicit_objects(schema)


def test_synthesis_manifest_keeps_auditable_hashes_without_row_level_indexes():
    from app.services.systemic_langgraph_bridge import _compact_shadow_dataset_manifest

    manifest = {
        "input_contract_version": CONTRACT_VERSION,
        "source_item_count": 2,
        "processed_item_count": 0,
        "coverage_status": "CAPTURED_COMPLETE",
        "dataset_hash": "d" * 64,
        "coverage_by_path": {"trade.id": {"present": 2, "null": 0}},
        "ordered_item_hashes": ["a" * 64, "b" * 64],
        "optional_missingness_by_path": {"trade.entry_risk_captured_at": 1},
        "shard_plan": [{
            "shard_index": 0,
            "item_count": 2,
            "item_ids": ["id-1", "id-2"],
            "item_hashes": ["a" * 64, "b" * 64],
            "payload_hash": "c" * 64,
            "payload_bytes": 100,
            "estimated_input_tokens": 84,
        }],
    }

    compact = _compact_shadow_dataset_manifest(manifest)

    assert compact["source_item_count"] == 2
    assert compact["coverage_path_count"] == 1
    assert compact["ordered_item_count"] == 2
    assert compact["coverage_by_path_hash"] == canonical_hash(manifest["coverage_by_path"])
    assert compact["ordered_item_hashes_hash"] == canonical_hash(manifest["ordered_item_hashes"])
    assert "coverage_by_path" not in compact
    assert "ordered_item_hashes" not in compact
    assert "item_ids" not in compact["shard_plan"][0]
    assert "item_hashes" not in compact["shard_plan"][0]


def test_synthesis_hashes_verbose_duplicate_ledgers_but_keeps_their_shape():
    from app.services.systemic_langgraph_bridge import (
        _compact_shadow_tool_evidence,
        _provider_decision_context,
    )

    large_data = {"indicator_buckets": [{"indicator": "rsi", "lift": 1.2}] * 100}
    evidence = SimpleNamespace(
        id=uuid.uuid4(), module_key="shadow_portfolio",
        tool_name="shadow.get_indicator_lift",
        output_json={
            "contract_version": "v1", "tool": "shadow.get_indicator_lift",
            "data": large_data, "quality": "COMPLETE",
        },
        quality="COMPLETE", freshness_json=None,
    )

    compact = _compact_shadow_tool_evidence(_provider_decision_context({}, [evidence]))
    output = compact["typed_tool_evidence"][0]["output"]

    assert output["data"]["provider_detail_status"] == "LEDGER_ONLY"
    assert output["data"]["collection_counts"] == {"indicator_buckets": 100}
    assert output["data"]["source_document_hash"] == canonical_hash(large_data)
    assert output["source_output_hash"] == canonical_hash(evidence.output_json)
    assert "indicator_buckets" not in output["data"]


def test_sharding_is_deterministic_and_keeps_each_trade_whole_once():
    dataset_id = uuid.uuid4()
    items = tuple(_item(index, padding=800) for index in range(5))
    left = plan_shards(dataset_snapshot_id=dataset_id, items=items, max_input_tokens=1_500)
    right = plan_shards(dataset_snapshot_id=dataset_id, items=items, max_input_tokens=1_500)
    assert [shard.payload_hash for shard in left] == [shard.payload_hash for shard in right]
    seen = [item.shadow_trade_id for shard in left for item in shard.items]
    assert seen == [item.shadow_trade_id for item in items]
    assert len(seen) == len(set(seen))


def test_sharding_respects_governed_item_limit_for_output_capacity():
    dataset_id = uuid.uuid4()
    items = tuple(_item(index) for index in range(5))

    plan = plan_shards(
        dataset_snapshot_id=dataset_id,
        items=items,
        max_input_tokens=100_000,
        max_items_per_shard=2,
    )

    assert [len(shard.items) for shard in plan] == [2, 2, 1]
    assert [item.shadow_trade_id for shard in plan for item in shard.items] == [
        item.shadow_trade_id for item in items
    ]


def test_sharding_rejects_invalid_governed_item_limit():
    with pytest.raises(ShadowCanonicalContractError) as exc_info:
        plan_shards(
            dataset_snapshot_id=uuid.uuid4(),
            items=(_item(0),),
            max_input_tokens=100_000,
            max_items_per_shard=0,
        )
    assert exc_info.value.code == "SHARD_ITEM_LIMIT_REQUIRED"


def test_single_trade_larger_than_context_fails_closed():
    with pytest.raises(ShadowCanonicalContractError) as exc_info:
        plan_shards(
            dataset_snapshot_id=uuid.uuid4(), items=(_item(0, padding=20_000),),
            max_input_tokens=100,
        )
    assert exc_info.value.code == "SHARD_CONTEXT_EXCEEDED"


def test_provider_payload_supplies_exact_persisted_item_hash_and_reconciles_once():
    dataset_id = uuid.uuid4()
    canonical_item = _item(0)
    plan = plan_shards(
        dataset_snapshot_id=dataset_id,
        items=(canonical_item,),
        max_input_tokens=10_000,
    )[0]
    persisted = AIDatasetSnapshotItemRecord(
        id=canonical_item.record_id,
        tenant_id=uuid.uuid4(),
        dataset_snapshot_id=dataset_id,
        report_run_id=uuid.uuid4(),
        shadow_trade_id=canonical_item.shadow_trade_id,
        report_position=0,
        canonical_json=canonical_item.payload,
        item_hash=canonical_item.item_hash,
        payload_bytes=canonical_item.payload_bytes,
        estimated_tokens=canonical_item.estimated_tokens,
    )
    shard = AIAnalysisShardRecord(
        id=plan.record_id,
        tenant_id=persisted.tenant_id,
        ai_request_id=uuid.uuid4(),
        dataset_snapshot_id=dataset_id,
        shard_index=0,
        status="COMPLETED",
        item_count=1,
        item_ids=[str(persisted.id)],
        item_hashes=[persisted.item_hash],
        payload_hash=plan.payload_hash,
        payload_bytes=plan.payload_bytes,
        estimated_input_tokens=plan.estimated_input_tokens,
        result_json={
            "processed_items": [{"id": str(persisted.shadow_trade_id), "hash": persisted.item_hash}],
            "evidence": [],
            "warnings": [],
        },
    )

    payload = provider_shard_payload(dataset_id, shard, [persisted])
    assert payload["items"] == [{
        "shadow_trade_id": str(persisted.shadow_trade_id),
        "item_hash": persisted.item_hash,
        "canonical_trade": canonical_item.payload,
    }]
    assert canonical_hash(payload) == shard.payload_hash
    reconcile_shard_results(expected_items=[persisted], shards=[shard])


def test_v2_provider_projection_keeps_decision_evidence_and_hashes_verbose_subtrees():
    dataset_id = uuid.uuid4()
    trade = _trade()
    trade.config_snapshot = {
        "final_score": 80,
        "l3_authorization_contract_v3": {
            "contract_version": "l3-authorization-v3",
            "final_decision": "REJECT",
            "feature_registry": [{"verbose_marker": "must-not-reach-provider"}],
            "feature_evaluations": [{
                "condition_id": "rsi-min",
                "indicator": "rsi",
                "actual": 61.2,
                "expected": 55,
                "operator": ">=",
                "result": False,
                "status": "REJECT",
                "source_timestamp": "omitted-verbose-provenance",
            }],
        },
        "feature_source_times": {"rsi": "2026-09-08T12:00:00Z"},
    }
    canonical = canonical_trade_payload(uuid.uuid4(), 0, trade)
    encoded = canonical_json(canonical).encode()
    item = CanonicalItem(
        record_id=uuid.uuid4(), shadow_trade_id=trade.id, report_position=0,
        payload=canonical, item_hash=canonical_hash(canonical), payload_bytes=len(encoded),
        estimated_tokens=1,
    )
    plan = plan_shards(
        dataset_snapshot_id=dataset_id,
        items=(item,),
        max_input_tokens=100_000,
    )[0]
    persisted = AIDatasetSnapshotItemRecord(
        id=item.record_id, tenant_id=trade.user_id, dataset_snapshot_id=dataset_id,
        report_run_id=uuid.uuid4(), shadow_trade_id=trade.id, report_position=0,
        canonical_json=canonical, item_hash=item.item_hash,
        payload_bytes=item.payload_bytes, estimated_tokens=item.estimated_tokens,
    )
    shard = AIAnalysisShardRecord(
        id=plan.record_id, tenant_id=trade.user_id, ai_request_id=uuid.uuid4(),
        dataset_snapshot_id=dataset_id, shard_index=0, status="PLANNED", item_count=1,
        item_ids=[str(item.record_id)], item_hashes=[item.item_hash],
        payload_hash=plan.payload_hash, payload_bytes=plan.payload_bytes,
        estimated_input_tokens=plan.estimated_input_tokens,
    )

    payload = provider_shard_payload(dataset_id, shard, [persisted])
    serialized = canonical_json(payload)
    provider_trade = payload["items"][0]["canonical_trade"]
    projected_auth = provider_trade["snapshots"]["configuration"][
        "l3_authorization_contract_v3"
    ]

    assert payload["provider_projection_contract_version"] == PROVIDER_PROJECTION_CONTRACT_VERSION
    assert provider_trade["canonical_payload_hash"] == item.item_hash
    assert projected_auth["final_decision"] == "REJECT"
    assert projected_auth["failed_feature_evaluations"][0]["actual"] == 61.2
    assert projected_auth["source_document_hash"] == canonical_hash(
        trade.config_snapshot["l3_authorization_contract_v3"]
    )
    assert "must-not-reach-provider" not in serialized
    assert "omitted-verbose-provenance" not in serialized


def test_provider_payload_and_reconciliation_fail_on_hash_divergence_or_duplicate():
    dataset_id = uuid.uuid4()
    canonical_item = _item(0)
    plan = plan_shards(
        dataset_snapshot_id=dataset_id,
        items=(canonical_item,),
        max_input_tokens=10_000,
    )[0]
    persisted = AIDatasetSnapshotItemRecord(
        id=canonical_item.record_id,
        tenant_id=uuid.uuid4(),
        dataset_snapshot_id=dataset_id,
        report_run_id=uuid.uuid4(),
        shadow_trade_id=canonical_item.shadow_trade_id,
        report_position=0,
        canonical_json={**canonical_item.payload, "tampered": True},
        item_hash=canonical_item.item_hash,
        payload_bytes=canonical_item.payload_bytes,
        estimated_tokens=canonical_item.estimated_tokens,
    )
    shard = AIAnalysisShardRecord(
        id=plan.record_id,
        tenant_id=persisted.tenant_id,
        ai_request_id=uuid.uuid4(),
        dataset_snapshot_id=dataset_id,
        shard_index=0,
        status="COMPLETED",
        item_count=1,
        item_ids=[str(persisted.id)],
        item_hashes=[persisted.item_hash],
        payload_hash=plan.payload_hash,
        payload_bytes=plan.payload_bytes,
        estimated_input_tokens=plan.estimated_input_tokens,
    )
    with pytest.raises(ShadowCanonicalContractError) as exc_info:
        provider_shard_payload(dataset_id, shard, [persisted])
    assert exc_info.value.code == "DATASET_RECONCILIATION_FAILED"

    persisted.canonical_json = canonical_item.payload
    shard.result_json = {
        "processed_items": [{"id": str(persisted.shadow_trade_id), "hash": "wrong-hash"}],
        "evidence": [],
        "warnings": [],
    }
    with pytest.raises(ShadowCanonicalContractError) as exc_info:
        reconcile_shard_results(expected_items=[persisted], shards=[shard])
    assert exc_info.value.code == "DATASET_RECONCILIATION_FAILED"


@pytest.mark.asyncio
async def test_shadow_provider_plan_carries_question_into_synthesis_context():
    from app.services.systemic_langgraph_bridge import _shadow_provider_plan

    canonical_item = _item(0)
    tenant_id = uuid.uuid4()
    dataset_id = uuid.uuid4()
    request_id = uuid.uuid4()
    persisted = AIDatasetSnapshotItemRecord(
        id=canonical_item.record_id,
        tenant_id=tenant_id,
        dataset_snapshot_id=dataset_id,
        report_run_id=uuid.uuid4(),
        shadow_trade_id=canonical_item.shadow_trade_id,
        report_position=0,
        canonical_json=canonical_item.payload,
        item_hash=canonical_item.item_hash,
        payload_bytes=canonical_item.payload_bytes,
        estimated_tokens=canonical_item.estimated_tokens,
    )
    shard_plan = plan_shards(
        dataset_snapshot_id=dataset_id,
        items=(canonical_item,),
        max_input_tokens=100_000,
    )[0]
    shard = AIAnalysisShardRecord(
        id=shard_plan.record_id,
        tenant_id=tenant_id,
        ai_request_id=request_id,
        dataset_snapshot_id=dataset_id,
        shard_index=shard_plan.shard_index,
        status="PLANNED",
        item_count=1,
        item_ids=[str(persisted.id)],
        item_hashes=[persisted.item_hash],
        payload_hash=shard_plan.payload_hash,
        payload_bytes=shard_plan.payload_bytes,
        estimated_input_tokens=shard_plan.estimated_input_tokens,
    )

    class _Rows:
        def __init__(self, rows):
            self.rows = rows

        def scalars(self):
            return self.rows

    class _DB:
        def __init__(self):
            self.results = [_Rows([persisted]), _Rows([shard])]

        async def execute(self, _query):
            return self.results.pop(0)

    question = "Diagnostique a causa raiz da seleção"
    plan = await _shadow_provider_plan(
        _DB(),
        request=SimpleNamespace(id=request_id, tenant_id=tenant_id),
        dataset=SimpleNamespace(id=dataset_id, row_count=1, context_manifest={}),
        question=question,
        provider="deepseek",
        shard_max_output_tokens=256,
        synthesis_max_output_tokens=256,
        request_token_limit=1_000_000,
        prompt=SimpleNamespace(
            system_template="System {question}",
            user_template="Evidence {evidence}",
            output_schema_json={
                "type": "object",
                "required": ["answer"],
                "properties": {"answer": {"type": "string"}},
                "additionalProperties": False,
            },
        ),
        bundle=SimpleNamespace(bundle_json={}),
        tool_evidence_rows=[],
    )

    assert plan["synthesis_base"]["question"] == question


@pytest.mark.asyncio
async def test_completed_shard_resume_is_idempotent_and_does_not_repeat_provider_call(monkeypatch):
    from app.ai_orchestration.runtime import ProviderResponse
    from app.services.systemic_langgraph_bridge import (
        SystemicLangGraphBridge,
        _execute_shadow_provider_plan,
        _shadow_synthesis_schema,
    )

    canonical_item = _item(0)
    persisted = AIDatasetSnapshotItemRecord(
        id=canonical_item.record_id,
        tenant_id=uuid.uuid4(),
        dataset_snapshot_id=uuid.uuid4(),
        report_run_id=uuid.uuid4(),
        shadow_trade_id=canonical_item.shadow_trade_id,
        report_position=0,
        canonical_json=canonical_item.payload,
        item_hash=canonical_item.item_hash,
        payload_bytes=canonical_item.payload_bytes,
        estimated_tokens=canonical_item.estimated_tokens,
    )
    shard_result = {
        "processed_items": [{"id": str(persisted.shadow_trade_id), "hash": persisted.item_hash}],
        "evidence": [],
        "warnings": [],
    }
    shard = AIAnalysisShardRecord(
        id=uuid.uuid4(),
        tenant_id=persisted.tenant_id,
        ai_request_id=uuid.uuid4(),
        dataset_snapshot_id=persisted.dataset_snapshot_id,
        shard_index=0,
        status="COMPLETED",
        item_count=1,
        item_ids=[str(persisted.id)],
        item_hashes=[persisted.item_hash],
        payload_hash="c" * 64,
        payload_bytes=100,
        estimated_input_tokens=50,
        tokens_input=11,
        tokens_output=7,
        result_json=shard_result,
    )
    base_schema = {
        "type": "object",
        "required": ["answer"],
        "properties": {"answer": {"type": "string"}},
        "additionalProperties": False,
    }
    calls = []

    async def _provider(**kwargs):
        calls.append(kwargs)
        return ProviderResponse(
            output={"answer": "ok", "referenced_shards": [0]},
            tokens_input=13,
            tokens_output=5,
        )

    monkeypatch.setattr(
        SystemicLangGraphBridge,
        "execute_json_provider",
        staticmethod(_provider),
    )

    class _DB:
        async def flush(self):
            return None

    prompt = SimpleNamespace(
        system_template="System {question}",
        user_template="Evidence {evidence}",
    )
    plan = {
        "items": [persisted],
        "shards": [shard],
        "shard_prompts": [{
            "record": shard,
            "system_prompt": "must not be called",
            "user_prompt": "must not be called",
        }],
        "synthesis_base": {
            "question": "question",
            "dataset_manifest": {"source_item_count": 1},
            "configuration_bundle": {},
            "deterministic_tool_evidence": {},
            "shard_evidence": [],
        },
        "synthesis_max_output_tokens": 256,
        "synthesis_schema": _shadow_synthesis_schema(base_schema, 1),
    }
    response = await _execute_shadow_provider_plan(
        _DB(),
        plan=plan,
        request=SimpleNamespace(id=shard.ai_request_id),
        provider="anthropic",
        model="model",
        api_key="not-used",
        shard_max_output_tokens=128,
        prompt=prompt,
    )

    assert len(calls) == 1
    assert calls[0]["request_id"].endswith(":synthesis")
    assert calls[0].get("thinking_mode") is None
    assert response.tokens_input == 24
    assert response.tokens_output == 12
    assert shard.status == "RECONCILED"
