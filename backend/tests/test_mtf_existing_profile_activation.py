from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest

from app.api import profiles as profiles_api
from app.services.mtf_profile_activation_service import (
    IMPORT_MODE,
    MTFActivationConflict,
    _validate_watchlist_chain,
    parse_activation_document,
)
from app.services.mtf_walk_forward import require_calibration_config
from app.services.mtf_walk_forward import candidate_grid, fit_candidate
from app.services.mtf_calibration_service import _profile_payloads


def _profile(layer: str) -> dict:
    timeframe = "1h" if layer == "L1" else "15m"
    role = "primary_filter" if layer == "L1" else "score_engine"
    profile_id = (
        "11111111-1111-1111-1111-111111111111"
        if layer == "L1"
        else "22222222-2222-2222-2222-222222222222"
    )
    return {
        "profile_id": profile_id,
        "expected_profile_version_id": (
            "33333333-3333-3333-3333-333333333333"
            if layer == "L1"
            else "44444444-4444-4444-4444-444444444444"
        ),
        "expected_profile_config_hash": ("a" if layer == "L1" else "b") * 64,
        "name": layer,
        "profile_kind": "MTF_LAYER",
        "layer": layer,
        "funnel_role": role,
        "default_timeframe": timeframe,
        "filters": {"logic": "AND", "conditions": []},
        "signals": {"logic": "AND", "conditions": []},
        "entry_triggers": {"logic": "AND", "conditions": []},
        "block_rules": {"blocks": []},
        "scoring": {"enabled": False, "rules": [], "selected_rule_ids": []},
        "mtf_semantics": {"configured": True},
        "source_identity": {
            "candle_policy": "CLOSED_ONLY",
            "allowed_source_providers": ["gate.io"],
            "provider_policy_id": "spot_closed_v1",
            "scheduler_group": "structural",
            "allowed_producer_versions": ["mtf_indicator_producer_v1"],
            "indicator_config_profile_id": "99999999-9999-9999-9999-999999999999",
            "indicator_config_hash": "9" * 64,
            "allowed_capture_contract_versions": ["spot_mtf_closed_ohlcv_v2"],
            "validity_margin_seconds": 60,
        },
        "calibration": {
            "status": "PASSED",
            "method": "WALK_FORWARD",
            "baseline_outperformed": True,
            "worst_fold_drawdown_not_worse": True,
            "min_samples": 100,
        },
    }


def _document() -> dict:
    return {
        "import_mode": IMPORT_MODE,
        "allow_create": False,
        "activation_mode": "SHADOW",
        "operational_effect": False,
        "profiles": {"L1": _profile("L1"), "L2": _profile("L2")},
        "watchlists": {
            "L1": "55555555-5555-5555-5555-555555555555",
            "L2": "66666666-6666-6666-6666-666666666666",
        },
        "expected_watchlist_bindings": {
            "L1": "11111111-1111-1111-1111-111111111111",
            "L2": "11111111-1111-1111-1111-111111111111",
        },
        "calibration": {
            "run_id": "77777777-7777-7777-7777-777777777777",
            "policy_hash": "c" * 64,
            "dataset_hash": "d" * 64,
            "thresholds_hashes": {"L1": "e" * 64, "L2": "f" * 64},
        },
        "l3_source_identity": {"required_indicators": ["price"]},
    }


def test_governed_document_is_update_only_shadow_and_keeps_layer_identity():
    parsed = parse_activation_document(_document())

    assert parsed["profiles"]["L1"]["config"]["default_timeframe"] == "1h"
    assert parsed["profiles"]["L2"]["config"]["default_timeframe"] == "15m"
    assert parsed["profiles"]["L1"]["profile_id"] != parsed["profiles"]["L2"]["profile_id"]
    assert parsed["profiles"]["L1"]["config"]["mtf_layer"] == {
        "layer": "L1", "activation_mode": "SHADOW", "operational_effect": False,
    }


@pytest.mark.parametrize(
    ("path", "value", "message"),
    [
        (("allow_create",), True, "allow_create must be false"),
        (("operational_effect",), True, "operational_effect must be false"),
        (("activation_mode",), "DRAFT", "activation_mode must be SHADOW"),
        (("profiles", "L1", "default_timeframe"), "5m", "L1_TIMEFRAME_MUST_BE_1h"),
    ],
)
def test_governed_document_rejects_unsafe_or_wrong_layer_values(path, value, message):
    document = _document()
    target = document
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value

    with pytest.raises(ValueError, match=message):
        parse_activation_document(document)


def test_governed_document_rejects_duplicate_profile_identity():
    document = _document()
    document["profiles"]["L2"]["profile_id"] = document["profiles"]["L1"]["profile_id"]

    with pytest.raises(ValueError, match="must be distinct"):
        parse_activation_document(document)


def test_mtf_routes_are_not_shadowed_by_dynamic_profile_route():
    paths = [route.path for route in profiles_api.router.routes]
    dynamic = paths.index("/api/profiles/{profile_id}")
    assert paths.index("/api/profiles/mtf/activation-preview") < dynamic
    assert paths.index("/api/profiles/mtf/activate-existing") < dynamic
    assert paths.index("/api/profiles/mtf/activation/{audit_id}/rollback") < dynamic


def test_governed_document_requires_watchlist_compare_and_swap_bindings():
    document = _document()
    document.pop("expected_watchlist_bindings")

    with pytest.raises(ValueError, match="expected_watchlist_bindings"):
        parse_activation_document(document)


def test_watchlist_chain_accepts_existing_pool_watchlist_origin():
    pool_id = UUID("77777777-7777-7777-7777-777777777777")
    l1_id = UUID("55555555-5555-5555-5555-555555555555")
    l1 = SimpleNamespace(
        id=l1_id,
        source_pool_id=None,
        source_watchlist_id=pool_id,
    )
    l2 = SimpleNamespace(
        source_pool_id=None,
        source_watchlist_id=l1_id,
    )
    pool = SimpleNamespace(id=pool_id, level="POOL", market_mode="spot")

    _validate_watchlist_chain(l1, l2, l1_source=pool)


def test_watchlist_chain_rejects_non_pool_watchlist_origin():
    source_id = UUID("77777777-7777-7777-7777-777777777777")
    l1_id = UUID("55555555-5555-5555-5555-555555555555")
    l1 = SimpleNamespace(
        id=l1_id,
        source_pool_id=None,
        source_watchlist_id=source_id,
    )
    l2 = SimpleNamespace(
        source_pool_id=None,
        source_watchlist_id=l1_id,
    )
    wrong_source = SimpleNamespace(
        id=source_id,
        level="L3",
        market_mode="spot",
    )

    with pytest.raises(MTFActivationConflict, match="SOURCE_WATCHLIST_INVALID"):
        _validate_watchlist_chain(l1, l2, l1_source=wrong_source)


def test_statistical_policy_proposal_is_validator_compatible_but_not_approved():
    proposal_path = (
        Path(__file__).resolve().parents[2]
        / "docs"
        / "MTF_CALIBRATION_POLICY_PROPOSAL_v1.json"
    )
    proposal = json.loads(proposal_path.read_text(encoding="utf-8"))
    assert proposal["approval_confirmed"] is False
    proposal.pop("approval_confirmed")
    proposal.update({
        "approval_status": "APPROVED",
        "approved_by": "00000000-0000-0000-0000-000000000001",
        "approved_at": "2026-09-05T18:30:24.457048-03:00",
    })

    validated = require_calibration_config(proposal)

    assert validated["candidate_search_mode"] == "BOUNDED_COORDINATE"
    assert validated["scope"]["timeframes"] == {
        "L1": "1h", "L2": "15m", "L3": "5m",
    }


def test_activation_audit_migration_keeps_rollback_actor_and_timestamp():
    migration = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "218_mtf_existing_profile_activation_audit.py"
    ).read_text(encoding="utf-8")
    assert "rolled_back_by UUID" in migration
    assert "rolled_back_at TIMESTAMPTZ" in migration
    assert "status IN ('APPLIED', 'ROLLED_BACK')" in migration


def test_calibrated_profiles_form_a_valid_governed_activation_document():
    proposal_path = (
        Path(__file__).resolve().parents[2]
        / "docs"
        / "MTF_CALIBRATION_POLICY_PROPOSAL_v1.json"
    )
    policy = json.loads(proposal_path.read_text(encoding="utf-8"))
    policy.pop("approval_confirmed")
    policy.update({
        "approval_status": "APPROVED",
        "approved_by": "00000000-0000-0000-0000-000000000001",
        "approved_at": "2026-09-05T18:30:24.457048-03:00",
    })
    policy = require_calibration_config(policy)
    train = [{
        "features": {
            item["feature"]: float(index + 1)
            for index, item in enumerate(policy["candidate_dimensions"])
            if item.get("feature")
        }
    } for _ in range(3)]
    fitted = fit_candidate(candidate_grid(policy)[0], train)
    emitted = _profile_payloads(
        policy=policy,
        fitted=fitted,
        run_id="77777777-7777-7777-7777-777777777777",
        policy_hash="c" * 64,
        results={"dataset_hash": "d" * 64},
    )
    document = _document()
    for layer in ("L1", "L2"):
        identity = {
            key: document["profiles"][layer][key]
            for key in (
                "profile_id", "expected_profile_version_id",
                "expected_profile_config_hash", "name", "profile_kind",
                "layer", "funnel_role",
            )
        }
        document["profiles"][layer] = {**emitted[layer], **identity}
        document["calibration"]["thresholds_hashes"][layer] = (
            emitted[layer]["calibration"]["thresholds_hash"]
        )

    parsed = parse_activation_document(document)

    assert parsed["profiles"]["L1"]["config"]["filters"]["conditions"]
    assert parsed["profiles"]["L2"]["config"]["filters"]["conditions"]
