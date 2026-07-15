from __future__ import annotations

import pytest

from app.ml.evidence_registry import validate_evidence
from app.services.calibration_orchestrator_v2 import (
    apply_recommendation_patch,
    apply_stable_patch,
    resolve_stable_path,
)
from app.services.ev_score_v2 import normalized_score


def test_stable_id_patch_preserves_incumbent_input() -> None:
    config = {
        "entry_triggers": {
            "conditions": [
                {"condition_id": "rsi-entry", "field": "rsi", "value": 30},
                {"condition_id": "adx-entry", "field": "adx", "value": 20},
            ]
        }
    }
    path = "/entry_triggers/conditions/by_id/rsi-entry/value"

    challenger = apply_stable_patch(config, path, 35)

    assert resolve_stable_path(challenger, path) == 35
    assert resolve_stable_path(config, path) == 30
    assert challenger["entry_triggers"]["conditions"][1]["value"] == 20


@pytest.mark.parametrize(
    "path,error",
    [
        ("entry_triggers/threshold", "target_path_must_be_absolute"),
        ("/entry_triggers/conditions/by_id/missing/value", "stable_id_not_found"),
        ("/entry_triggers/conditions/0/value", "target_path_not_found"),
    ],
)
def test_unstable_or_missing_paths_fail_closed(path: str, error: str) -> None:
    config = {
        "entry_triggers": {
            "conditions": [{"condition_id": "rsi-entry", "value": 30}]
        }
    }
    with pytest.raises(ValueError, match=error):
        apply_stable_patch(config, path, 35)


def test_add_and_remove_rules_require_stable_ids() -> None:
    config = {"block_rules": {"blocks": [{"rule_id": "spread", "value": 1}]}}
    added = apply_recommendation_patch(
        config, "ADD_BLOCK_RULE", "/block_rules/blocks",
        {"rule_id": "volatility", "value": 2},
    )
    assert [item["rule_id"] for item in added["block_rules"]["blocks"]] == [
        "spread", "volatility"
    ]
    removed = apply_recommendation_patch(
        added, "REMOVE_RULE", "/block_rules/blocks/by_id/spread", None
    )
    assert removed["block_rules"]["blocks"] == [{"rule_id": "volatility", "value": 2}]
    assert config["block_rules"]["blocks"] == [{"rule_id": "spread", "value": 1}]


def test_evidence_contract_rejects_bad_scope_interval_and_effective_n() -> None:
    payload = {
        "source_version": "math-v2",
        "dataset_hash": "sha256:test",
        "window_from": "2026-07-01T00:00:00Z",
        "window_to": "2026-07-10T00:00:00Z",
        "target_path": "scoring/thresholds/buy",
        "indicator": "rsi",
        "operator": ">=",
        "ci95_lower": 0.2,
        "ci95_upper": 0.1,
        "raw_n": 10,
        "effective_n": 11,
        "independent_windows": 2,
        "symbols": 4,
        "confidence": 1.1,
    }
    assert validate_evidence(payload) == [
        "effective_n_exceeds_raw_n",
        "target_path_must_be_absolute",
        "invalid_confidence_interval",
        "confidence_out_of_range",
    ]


def test_ev_score_uses_configured_economic_scale() -> None:
    config = {"ev_at_score_0": -0.01, "ev_at_score_100": 0.01}
    assert normalized_score(-0.01, config) == 0
    assert normalized_score(0.0, config) == 50
    assert normalized_score(0.01, config) == 100
    assert normalized_score(1.0, config) == 100


def test_ev_score_rejects_invalid_configuration() -> None:
    with pytest.raises(ValueError, match="invalid_ev_score_normalization"):
        normalized_score(0, {"ev_at_score_0": 1, "ev_at_score_100": 1})
