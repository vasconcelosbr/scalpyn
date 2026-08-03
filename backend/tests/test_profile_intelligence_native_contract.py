from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.ml.feature_contract_v2 import CAPTURE_CONTRACT_VERSION, snapshot_hash
from app.services.algorithm_governance_service import suggestion_registry_block_reasons
from app.services.profile_intelligence_contract import (
    OFFICIAL_CAPTURE_COLUMNS,
    filter_hash_valid_rows,
    official_where,
)


def _official_row(**overrides):
    snapshot = {"atr_pct": 1.2, "rsi": 50.0}
    values = {
        "source": "L3",
        "profile_id": "00000000-0000-0000-0000-000000000001",
        "decision_id": "00000000-0000-0000-0000-000000000002",
        "features_snapshot": snapshot,
        "feature_source_at": datetime(2026, 7, 12, 23, 59, tzinfo=timezone.utc),
        "feature_source_times": {
            "atr_pct": "2026-07-12T23:59:00+00:00",
            "rsi": "2026-07-12T23:59:00+00:00",
        },
        "features_captured_at": datetime(2026, 7, 13, tzinfo=timezone.utc),
        "entry_timestamp": datetime(2026, 7, 13, tzinfo=timezone.utc),
        "feature_hash": snapshot_hash(snapshot),
        "feature_extractor_version": "feature-engine-v2",
        "feature_schema_version": "entry_features_v2",
        "capture_contract_version": CAPTURE_CONTRACT_VERSION,
        "label_contract_version": "positive_net_return_v1",
        "profile_version_id": "00000000-0000-0000-0000-000000000003",
        "score_engine_version_id": "00000000-0000-0000-0000-000000000004",
        "lineage_status": "EXACT",
        "eligible_for_training": True,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_official_sql_contract_is_fail_closed():
    clause = official_where("st")
    assert f"capture_contract_version = '{CAPTURE_CONTRACT_VERSION}'" in clause
    assert "feature_source_at IS NOT NULL" in clause
    assert "feature_source_times IS NOT NULL" in clause
    assert "label_contract_version = 'positive_net_return_v1'" in clause
    assert "profile_version_id IS NOT NULL" in clause
    assert "score_engine_version_id IS NOT NULL" in clause
    assert "lineage_status = 'EXACT'" in clause
    assert "eligible_for_training IS TRUE" in clause
    assert "decision_id" in OFFICIAL_CAPTURE_COLUMNS


def test_hash_is_recomputed_before_profile_intelligence_use(monkeypatch):
    monkeypatch.setenv("NATIVE_CAPTURE_START_AT", "2026-07-12T18:21:57Z")
    good = _official_row()
    bad = _official_row(feature_hash="bad-hash")

    valid, invalid = filter_hash_valid_rows([good, bad])

    assert valid == [good]
    assert invalid == 1


def test_legacy_suggestion_cannot_create_profile():
    suggestion = SimpleNamespace(
        source_type="counterfactual_dynamic",
        source_run_id="run",
        profile_id="profile",
        diff_json={"before": {}, "after": {}},
        rollback_payload={"action": "archive"},
        validation_status="validated",
        actionability_status="validated",
        dataset_version="pi-run:legacy",
        feature_schema_version="shadow_features_snapshot:v1",
        label_version="shadow_outcome:v1",
    )

    reasons = suggestion_registry_block_reasons(suggestion)

    assert "dataset_not_official_native" in reasons
    assert "feature_schema_not_official" in reasons


def test_live_engine_has_no_direct_profile_config_update():
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[1]
        / "app"
        / "services"
        / "profile_intelligence_live_service.py"
    ).read_text(encoding="utf-8")
    assert "UPDATE profiles\n                    SET config = jsonb_set" not in source
    assert 't.outcome == "TP_HIT"' in source


def test_manual_pi_task_can_skip_ml_training_explicitly():
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[1]
        / "app"
        / "tasks"
        / "profile_intelligence_job.py"
    ).read_text(encoding="utf-8")
    assert "include_ml_challengers: bool = True" in source
    assert "if include_ml_challengers:" in source
    assert "ML challengers skipped explicitly" in source
