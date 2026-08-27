from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import re

import pytest

from app.services.l3_gate_compiler_v2 import evaluate_l3_gate_v2
from app.services.profile_execution_contract import (
    build_execution_contract_snapshot,
    required_condition_contract,
)
from app.services.profile_runtime_config import (
    canonical_hash,
    canonical_profile_config_hash,
    merge_profile_runtime_block_config,
)


NOW = datetime(2026, 8, 26, tzinfo=timezone.utc)
PROFILE_ID = "11111111-1111-1111-1111-111111111111"
VERSION_ID = "22222222-2222-2222-2222-222222222222"


def _config(*, include_adx=True, adx_value=14):
    triggers = [
        {
            "indicator": "obv",
            "operator": ">",
            "value": 0,
            "period": 20,
            "required": True,
            "enabled": True,
        }
    ]
    if include_adx:
        triggers.append(
            {
                "indicator": "adx",
                "operator": ">=",
                "value": adx_value,
                "period": 14,
                "required": True,
                "enabled": True,
            }
        )
    return {
        "default_timeframe": "5m",
        "filters": {"logic": "AND", "conditions": []},
        "signals": {
            "logic": "AND",
            "conditions": [{"field": "score", "operator": ">=", "value": 0}],
        },
        "entry_triggers": {"logic": "AND", "conditions": triggers},
        "block_rules": {"blocks": []},
        "scoring": {},
    }


def _snapshot(profile, version):
    return build_execution_contract_snapshot(
        profile_id=PROFILE_ID,
        profile_name="L3_OBV_ACCUMULATION_START_V1",
        profile_config=profile,
        profile_version_id=VERSION_ID,
        version_profile_id=PROFILE_ID,
        version_config=version,
        version_config_hash=canonical_hash(version),
        active_version_count=1,
    )


def _evaluate(profile, *, adx):
    return evaluate_l3_gate_v2(
        asset={"symbol": "TEST_USDT", "indicators": {"obv": 10, "adx": adx}},
        profile_config=profile,
        score=99,
        score_context={},
        evaluated_at=NOW,
        base_eligible=True,
        legacy_decision="ALLOW",
        block_rules_audit={},
    )


def test_canonical_hash_normalizes_numbers_but_preserves_array_order():
    assert canonical_hash({"value": 14, "nested": {"x": 1}}) == canonical_hash(
        {"nested": {"x": 1.0}, "value": 14.0}
    )
    assert canonical_hash({"items": [1, 2]}) != canonical_hash({"items": [2, 1]})


def test_profile_hash_excludes_only_catalogued_runtime_metadata():
    config = _config()
    augmented = {
        **deepcopy(config),
        "_execution_contract": {"status": "MATCH"},
        "_global_entry_triggers": {"conditions": []},
    }

    assert canonical_profile_config_hash(config) == canonical_profile_config_hash(
        augmented
    )
    assert canonical_profile_config_hash({**config, "custom_metadata": 1}) != (
        canonical_profile_config_hash(config)
    )


def test_contract_detects_required_adx_removed_from_runtime_projection():
    snapshot = _snapshot(_config(include_adx=False), _config(include_adx=True))

    assert snapshot["status"] == "MISMATCH"
    assert "CONFIG_CONTRACT_MISMATCH" in snapshot["reason_codes"]
    assert "REQUIRED_CONDITION_MISSING" in snapshot["reason_codes"]
    assert [item["indicator"] for item in snapshot["missing_required_conditions"]] == [
        "adx"
    ]


def test_contract_matches_exact_round_trip_snapshot():
    config = _config()
    snapshot = _snapshot(config, deepcopy(config))

    assert snapshot["contract_valid"] is True
    assert snapshot["status"] == "MATCH"
    assert all(section["match"] for section in snapshot["sections"].values())
    assert snapshot["required_conditions_hash"] == snapshot[
        "expected_required_conditions_hash"
    ]


def test_missing_immutable_version_fails_closed_contract():
    snapshot = build_execution_contract_snapshot(
        profile_id=PROFILE_ID,
        profile_name="L3_TEST",
        profile_config=_config(),
        profile_version_id=None,
        version_profile_id=None,
        version_config=None,
        version_config_hash=None,
    )

    assert snapshot["contract_valid"] is False
    assert "PROFILE_VERSION_MISSING" in snapshot["reason_codes"]


@pytest.mark.parametrize("adx", [13.42, 12.63, 11.53])
def test_adx_below_required_boundary_is_denied(adx):
    result = _evaluate(_config(), adx=adx)

    assert result["shadow_decision"] == "BLOCK"
    assert result["entry_triggers"]["failed_required"] == ["adx"]


def test_adx_at_boundary_allows_remaining_gates_to_continue():
    result = _evaluate(_config(), adx=14.0)

    assert result["entry_triggers"]["gate_passed"] is True
    assert result["shadow_decision"] == "ALLOW"


def test_missing_adx_is_required_skip_and_denied():
    result = evaluate_l3_gate_v2(
        asset={"symbol": "TEST_USDT", "indicators": {"obv": 10}},
        profile_config=_config(),
        score=99,
        score_context={},
        evaluated_at=NOW,
        base_eligible=True,
        legacy_decision="ALLOW",
        block_rules_audit={},
    )

    assert result["shadow_decision"] == "BLOCK"
    assert result["entry_triggers"]["skipped_required"] == ["adx"]


def test_invalid_required_operator_is_denied():
    profile = _config()
    profile["entry_triggers"]["conditions"][1]["operator"] = "teleports_above"

    result = _evaluate(profile, adx=99)

    assert result["entry_triggers"]["gate_passed"] is False
    assert result["entry_triggers"]["failed_required"] == ["adx"]
    condition = result["entry_triggers"]["conditions"][1]
    assert condition["operator"] == "teleports_above"
    assert condition["reason_code"]


def test_invalid_execution_contract_blocks_even_with_score_99_when_operational():
    profile = _config()
    profile["_execution_contract"] = {
        "contract_valid": False,
        "operational_effect": True,
        "status": "MISMATCH",
        "reason_codes": ["CONFIG_CONTRACT_MISMATCH"],
    }

    result = _evaluate(profile, adx=14)

    assert result["technical_would_authorize"] is True
    assert result["contract_would_authorize"] is False
    assert result["shadow_decision"] == "BLOCK"
    assert "PROFILE_EXECUTION_CONTRACT_FAILED" in result["reason_codes"]


def test_global_entry_triggers_are_an_additional_and_gate():
    profile = merge_profile_runtime_block_config(
        _config(),
        {
            "entry_triggers": {
                "logic": "AND",
                "conditions": [
                    {
                        "indicator": "rsi",
                        "operator": "<=",
                        "value": 60,
                        "required": True,
                    }
                ],
            }
        },
    )
    result = evaluate_l3_gate_v2(
        asset={
            "symbol": "TEST_USDT",
            "indicators": {"obv": 10, "adx": 14, "rsi": 70},
        },
        profile_config=profile,
        score=99,
        score_context={},
        evaluated_at=NOW,
        base_eligible=True,
        legacy_decision="ALLOW",
        block_rules_audit={},
    )

    assert result["entry_triggers"]["gate_passed"] is True
    assert result["global_entry_triggers"]["gate_passed"] is False
    assert result["shadow_decision"] == "BLOCK"


def test_required_condition_fingerprints_include_full_identity():
    required = required_condition_contract(_config())

    adx = next(item for item in required if item["indicator"] == "adx")
    assert adx["operator"] == ">="
    assert adx["value"] == 14
    assert adx["period"] == 14
    assert adx["fingerprint"]


def test_frozen_v1_snapshot_cannot_mix_with_later_v2_mutation():
    v1 = _config(adx_value=14)
    frozen_v1 = _snapshot(v1, v1)

    v2 = deepcopy(v1)
    v2["entry_triggers"]["conditions"][1]["value"] = 99
    frozen_v2 = build_execution_contract_snapshot(
        profile_id=PROFILE_ID,
        profile_name="L3_OBV_ACCUMULATION_START_V1",
        profile_config=v2,
        profile_version_id="33333333-3333-3333-3333-333333333333",
        version_profile_id=PROFILE_ID,
        version_config=v2,
        version_config_hash=canonical_hash(v2),
        active_version_count=1,
    )

    assert frozen_v1["profile_version_id"] == VERSION_ID
    assert frozen_v1["profile_projection"]["entry_triggers"]["conditions"][1]["value"] == 14
    assert frozen_v2["profile_projection"]["entry_triggers"]["conditions"][1]["value"] == 99


def test_profile_config_writes_are_centralized_in_contract_service():
    app_root = Path(__file__).resolve().parents[1] / "app"
    offenders = []
    for path in app_root.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        if path.name != "profile_execution_contract.py" and (
            re.search(r"\b(?:profile|resource)\.config\s*=(?!=)", source)
            or "UPDATE profiles\n        SET config" in source
            or "UPDATE profiles SET config" in source
        ):
            offenders.append(str(path.relative_to(app_root)))

    assert offenders == []
