from copy import deepcopy

import pytest

from app.services.profile_intelligence_manual_service import (
    MANUAL_ACTIONS,
    apply_manual_action,
    validate_manual_target,
)


@pytest.fixture
def config():
    return {
        "signals": {"conditions": [{"condition_id": "sig-1", "field": "rsi", "operator": ">", "value": 50}]},
        "scoring": {
            "generated_rules": [{"rule_id": "score-1", "indicator": "adx", "score": 5}],
            "weights": {"momentum": 20}, "thresholds": {"allow": 60},
        },
        "block_rules": {"blocks": [{"rule_id": "block-1", "indicator": "spread_pct", "value": 0.5}]},
    }


def test_all_contract_actions_are_exposed():
    assert MANUAL_ACTIONS == {
        "ADD_SIGNAL_CONDITION", "UPDATE_SIGNAL_THRESHOLD", "UPDATE_SIGNAL_RANGE",
        "REMOVE_SIGNAL_CONDITION", "ADD_SCORE_BONUS", "ADD_SCORE_PENALTY",
        "UPDATE_SCORE_WEIGHT", "UPDATE_SCORE_THRESHOLD", "ADD_BLOCK_RULE",
        "UPDATE_BLOCK_RULE", "REMOVE_BLOCK_RULE", "OBSERVE_ONLY",
    }


def test_add_score_penalty_is_copy_on_write_and_uses_stable_id(config):
    original = deepcopy(config)
    rule = {"rule_id": "penalty-volume", "indicator": "volume_spike", "score": -10}
    result = apply_manual_action(config, "ADD_SCORE_PENALTY", "/scoring/generated_rules", None, rule)
    assert config == original
    assert result["scoring"]["generated_rules"][-1] == rule


def test_update_signal_threshold_requires_exact_current_value(config):
    result = apply_manual_action(
        config, "UPDATE_SIGNAL_THRESHOLD", "/signals/conditions/by_id/sig-1/value", 50, 55,
    )
    assert result["signals"]["conditions"][0]["value"] == 55
    with pytest.raises(ValueError, match="current_value_mismatch"):
        apply_manual_action(config, "UPDATE_SIGNAL_THRESHOLD", "/signals/conditions/by_id/sig-1/value", 49, 55)


def test_remove_block_rule_requires_stable_path_and_exact_snapshot(config):
    current = config["block_rules"]["blocks"][0]
    result = apply_manual_action(config, "REMOVE_BLOCK_RULE", "/block_rules/blocks/by_id/block-1", current, None)
    assert result["block_rules"]["blocks"] == []
    with pytest.raises(ValueError, match="remove_requires_stable_id_path"):
        validate_manual_target("REMOVE_BLOCK_RULE", "/block_rules/blocks", None)


@pytest.mark.parametrize("path", [
    "/training/threshold", "/scoring/dataset", "/signals/features_snapshot",
    "/block_rules/historical", "/scoring/generated_rules/0/score",
])
def test_ml_historical_and_list_index_targets_fail_closed(path):
    with pytest.raises(ValueError):
        validate_manual_target("UPDATE_SCORE_THRESHOLD", path, 1)


def test_statistical_warnings_do_not_participate_in_patch_gate(config):
    # Gates such as max_single_day_share belong to the persisted warning list;
    # the bounded patch depends only on action/path/current/payload invariants.
    result = apply_manual_action(config, "UPDATE_SCORE_WEIGHT", "/scoring/weights/momentum", 20, 15)
    assert result["scoring"]["weights"]["momentum"] == 15


def test_observe_only_is_identical_copy_and_rejects_payload(config):
    result = apply_manual_action(config, "OBSERVE_ONLY", None, None, None)
    assert result == config and result is not config
    with pytest.raises(ValueError, match="observe_only_cannot_mutate"):
        apply_manual_action(config, "OBSERVE_ONLY", "/signals", None, {})


def test_manual_api_contract_routes_are_registered_before_uuid_detail():
    from app.api.profile_intelligence import router

    routes = [(route.path, next(iter(route.methods or []), "")) for route in router.routes]
    expected = {
        ("/api/profile-intelligence/manual-adjustments", "POST"),
        ("/api/profile-intelligence/manual-adjustments", "GET"),
        ("/api/profile-intelligence/manual-adjustments/{adjustment_id}", "GET"),
        ("/api/profile-intelligence/manual-adjustments/{adjustment_id}", "PUT"),
        ("/api/profile-intelligence/manual-adjustments/{adjustment_id}/preview", "POST"),
        ("/api/profile-intelligence/manual-adjustments/{adjustment_id}/approve-and-apply", "POST"),
        ("/api/profile-intelligence/manual-adjustments/{adjustment_id}/reject", "POST"),
        ("/api/profile-intelligence/manual-adjustments/{adjustment_id}/rollback", "POST"),
    }
    assert expected.issubset(set(routes))
    paths = [path for path, _ in routes]
    assert paths.index("/api/profile-intelligence/manual-adjustments/eligible-profiles") < paths.index("/api/profile-intelligence/manual-adjustments/{adjustment_id}")
