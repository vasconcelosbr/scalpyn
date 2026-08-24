from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
import uuid

import pytest

from app.ai_orchestration.hashing import canonical_hash
from app.models.shadow_trade import ShadowTrade
from app.models.systemic_ai import AIAnalysisShardRecord, AIDatasetSnapshotItemRecord
from app.services.shadow_full_canonical_service import (
    CONTRACT_VERSION,
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


def _item(position: int, padding: int = 0) -> CanonicalItem:
    payload = {
        "input_contract_version": CONTRACT_VERSION,
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


def test_missing_required_indicator_blocks_capture():
    trade = _trade()
    trade.features_snapshot = {**trade.features_snapshot, "ema21_distance_pct": None}
    with pytest.raises(ShadowCanonicalContractError) as exc_info:
        canonical_trade_payload(uuid.uuid4(), 0, trade)
    assert exc_info.value.code == "REQUIRED_FIELD_MISSING"
    assert "snapshots.entry_features.ema21_distance_pct" in exc_info.value.details["paths"]


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
def test_non_terminal_risk_snapshot_blocks_capture(status: str):
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

    with pytest.raises(ShadowCanonicalContractError) as exc_info:
        canonical_trade_payload(uuid.uuid4(), 0, trade)

    assert "snapshots.entry_risk.contract_status.status" in exc_info.value.details["paths"]


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


def test_sharding_is_deterministic_and_keeps_each_trade_whole_once():
    dataset_id = uuid.uuid4()
    items = tuple(_item(index, padding=800) for index in range(5))
    left = plan_shards(dataset_snapshot_id=dataset_id, items=items, max_input_tokens=1_500)
    right = plan_shards(dataset_snapshot_id=dataset_id, items=items, max_input_tokens=1_500)
    assert [shard.payload_hash for shard in left] == [shard.payload_hash for shard in right]
    seen = [item.shadow_trade_id for shard in left for item in shard.items]
    assert seen == [item.shadow_trade_id for item in items]
    assert len(seen) == len(set(seen))


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
            "processed_items": [{
                "shadow_trade_id": str(persisted.shadow_trade_id),
                "item_hash": persisted.item_hash,
            }],
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
        "processed_items": [
            {"shadow_trade_id": str(persisted.shadow_trade_id), "item_hash": persisted.item_hash},
            {"shadow_trade_id": str(persisted.shadow_trade_id), "item_hash": persisted.item_hash},
        ],
        "evidence": [],
        "warnings": [],
    }
    with pytest.raises(ShadowCanonicalContractError) as exc_info:
        reconcile_shard_results(expected_items=[persisted], shards=[shard])
    assert exc_info.value.code == "DATASET_RECONCILIATION_FAILED"


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
        "processed_items": [{
            "shadow_trade_id": str(persisted.shadow_trade_id),
            "item_hash": persisted.item_hash,
        }],
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
    assert response.tokens_input == 24
    assert response.tokens_output == 12
    assert shard.status == "RECONCILED"
