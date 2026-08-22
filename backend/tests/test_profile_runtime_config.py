"""Regression coverage for effective L3 profile runtime configuration."""

from app.services.block_engine import BlockEngine
from app.services.profile_runtime_config import merge_profile_runtime_block_config


PROFILE_ENTRY_TRIGGERS = {
    "logic": "AND",
    "conditions": [
        {
            "id": "uni-bb-width",
            "indicator": "bb_width",
            "operator": "between",
            "min": 0.03,
            "max": 0.12,
            "required": True,
            "enabled": True,
        },
        {
            "id": "uni-macd-histogram",
            "indicator": "macd_histogram",
            "operator": ">",
            "value": 0,
            "required": True,
            "enabled": True,
        },
    ],
}


def test_empty_global_entry_triggers_cannot_erase_profile_triggers():
    profile = {
        "entry_triggers": PROFILE_ENTRY_TRIGGERS,
        "block_rules": {"blocks": [{"id": "profile-block"}]},
    }
    global_block = {
        "entry_triggers": {"logic": "AND", "conditions": []},
        "block_rules": {"blocks": [{"id": "global-block"}]},
    }

    effective = merge_profile_runtime_block_config(profile, global_block)

    assert effective["entry_triggers"] == PROFILE_ENTRY_TRIGGERS
    assert effective["block_rules"] == global_block["block_rules"]


def test_nonempty_global_entry_triggers_remain_an_intentional_override():
    global_triggers = {
        "logic": "AND",
        "conditions": [
            {
                "id": "autopilot-adx",
                "indicator": "adx",
                "operator": ">=",
                "value": 25,
                "required": True,
                "enabled": True,
            }
        ],
    }

    effective = merge_profile_runtime_block_config(
        {"entry_triggers": PROFILE_ENTRY_TRIGGERS},
        {"entry_triggers": global_triggers},
    )

    assert effective["entry_triggers"] == global_triggers


def test_uni_snapshot_is_blocked_after_safe_runtime_merge():
    effective = merge_profile_runtime_block_config(
        {"entry_triggers": PROFILE_ENTRY_TRIGGERS},
        {"entry_triggers": {"logic": "AND", "conditions": []}},
    )
    engine = BlockEngine(
        {
            "blocks": [],
            "entry_triggers": effective["entry_triggers"],
        }
    )

    result = engine.evaluate_entry(
        {
            "bb_width": 0.023508,
            "macd_histogram": -0.00173363,
            "macd_histogram_pct": -0.040724,
        }
    )

    assert result["allowed"] is False
    assert set(result["failed_required"]) == {
        "uni-bb-width",
        "uni-macd-histogram",
    }
