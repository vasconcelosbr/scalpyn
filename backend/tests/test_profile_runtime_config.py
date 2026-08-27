"""Regression coverage for effective L3 profile runtime configuration."""

from copy import deepcopy

import pytest

from app.services.block_engine import BlockEngine
from app.services.profile_runtime_config import (
    BLOCK_RULE_CONFIG_CONFLICT,
    BlockRuleConfigConflict,
    canonical_hash,
    merge_block_rules,
    merge_profile_runtime_block_config,
)


PROFILE_ENTRY_TRIGGERS = {
    "logic": "AND",
    "conditions": [
        {
            "indicator": "bb_width",
            "operator": "between",
            "min": 0.03,
            "max": 0.12,
            "required": True,
            "enabled": True,
        },
        {
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
    assert effective["block_rules"]["blocks"] == [
        {"id": "global-block"},
        {"id": "profile-block"},
    ]


def test_nonempty_global_entry_triggers_are_separate_and_never_replace_profile():
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

    assert effective["entry_triggers"] == PROFILE_ENTRY_TRIGGERS
    assert effective["_global_entry_triggers"] == global_triggers
    assert effective["_entry_triggers_lineage"]["composition"] == "AND"
    assert effective["_entry_triggers_lineage"]["profile_trigger_count"] == 2
    assert effective["_entry_triggers_lineage"]["global_trigger_count"] == 1


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
        "bb_width",
        "macd_histogram",
    }


def _rule(rule_id, *, value=10, name=None):
    return {
        "id": rule_id,
        "name": name or rule_id,
        "enabled": True,
        "logic": "AND",
        "timeframe": "5m",
        "conditions": [
            {
                "indicator": "rsi",
                "operator": ">=",
                "value": value,
            }
        ],
    }


@pytest.mark.parametrize(
    ("global_blocks", "profile_blocks", "expected_ids"),
    [
        ([], [_rule("A"), _rule("B")], ["A", "B"]),
        ([_rule("G1"), _rule("G2")], [], ["G1", "G2"]),
        (
            [_rule("G1"), _rule("G2")],
            [_rule("P1"), _rule("P2")],
            ["G1", "G2", "P1", "P2"],
        ),
    ],
)
def test_merge_block_rules_preserves_global_first_deterministic_order(
    global_blocks, profile_blocks, expected_ids
):
    effective = merge_block_rules(
        global_blocks=global_blocks,
        profile_blocks=profile_blocks,
    )

    assert [rule["id"] for rule in effective] == expected_ids


def test_merge_block_rules_deduplicates_exact_rule():
    same_rule = _rule("A")

    effective = merge_block_rules(
        global_blocks=[same_rule],
        profile_blocks=[deepcopy(same_rule)],
    )

    assert effective == [same_rule]


def test_canonical_hash_normalizes_equivalent_numeric_json_values():
    assert canonical_hash({"value": -0.0, "threshold": 1.0}) == canonical_hash(
        {"threshold": 1, "value": 0}
    )


def test_rule_without_id_uses_structural_identity_and_deduplicates():
    same_rule = _rule("unused")
    same_rule.pop("id")

    effective = merge_block_rules(
        global_blocks=[same_rule],
        profile_blocks=[deepcopy(same_rule)],
    )

    assert effective == [same_rule]


def test_merge_block_rules_rejects_same_id_with_different_definition():
    with pytest.raises(BlockRuleConfigConflict) as exc_info:
        merge_block_rules(
            global_blocks=[_rule("X", value=10)],
            profile_blocks=[_rule("X", value=20)],
            profile_id="profile-1",
            profile_version_id="version-1",
        )

    exc = exc_info.value
    assert exc.code == BLOCK_RULE_CONFIG_CONFLICT
    assert exc.rule_id == "X"
    assert exc.global_definition_hash != exc.profile_definition_hash
    assert exc.profile_id == "profile-1"
    assert exc.profile_version_id == "version-1"


def test_merge_persists_three_distinct_lineage_hashes_without_mutating_inputs():
    profile = {"block_rules": {"blocks": [_rule("P")]}}
    global_config = {"block_rules": {"blocks": [_rule("G")]}}
    profile_before = deepcopy(profile)
    global_before = deepcopy(global_config)

    effective = merge_profile_runtime_block_config(
        profile,
        global_config,
        profile_id="profile-1",
        profile_version_id="version-1",
    )
    lineage = effective["_block_rules_lineage"]

    assert profile == profile_before
    assert global_config == global_before
    assert lineage["profile_id"] == "profile-1"
    assert lineage["profile_version_id"] == "version-1"
    assert lineage["profile_block_rules_hash"] != lineage["global_block_rules_hash"]
    assert lineage["effective_block_rules_hash"] not in {
        lineage["profile_block_rules_hash"],
        lineage["global_block_rules_hash"],
    }
    assert lineage["profile_rules_count"] == 1
    assert lineage["global_rules_count"] == 1
    assert lineage["effective_rules_count"] == 2


VIRTUAL_ORIGINAL = {
    "name": "Exaustao curta por RSI",
    "enabled": True,
    "logic": "AND",
    "timeframe": "5m",
    "conditions": [
        {"indicator": "entry_exhaustion_score", "operator": ">=", "value": 68},
        {"indicator": "rsi_6", "operator": ">=", "value": 75, "period": 6},
    ],
}
VIRTUAL_ADJUSTED = {
    **deepcopy(VIRTUAL_ORIGINAL),
    "conditions": [
        *deepcopy(VIRTUAL_ORIGINAL["conditions"]),
        {"indicator": "macd_hist_slope_3", "operator": "<=", "value": 0},
    ],
}
ASTER_ADJUSTED = {
    "name": "Extremo comprador com desaceleracao imediata",
    "enabled": True,
    "logic": "AND",
    "timeframe": "5m",
    "conditions": [
        {"indicator": "stoch_k", "operator": ">=", "value": 90},
        {"indicator": "rsi", "operator": ">=", "value": 68},
        {"indicator": "macd_histogram_slope", "operator": "<", "value": 0},
    ],
}
AVAX_ADJUSTED = {
    "name": "Reload fraco com compressao e book vendedor",
    "enabled": True,
    "logic": "AND",
    "timeframe": "5m",
    "conditions": [
        {"indicator": "adx", "operator": "<", "value": 20},
        {"indicator": "atr_pct", "operator": "<", "value": 0.18},
        {"indicator": "bb_width", "operator": "<", "value": 0.01},
        {"indicator": "orderbook_pressure", "operator": "<", "value": -0.15},
    ],
}


@pytest.mark.parametrize(
    ("rule", "metrics", "expected_name"),
    [
        (
            VIRTUAL_ORIGINAL,
            {"entry_exhaustion_score": 69.1, "rsi_6": 77.72},
            "Exaustao curta por RSI",
        ),
        (
            VIRTUAL_ADJUSTED,
            {
                "entry_exhaustion_score": 69.1,
                "rsi_6": 77.72,
                "macd_hist_slope_3": -0.01157,
            },
            "Exaustao curta por RSI",
        ),
        (
            ASTER_ADJUSTED,
            {
                "stoch_k": 91.14,
                "rsi": 68.81,
                "macd_histogram_slope": -0.00006214,
            },
            "Extremo comprador com desaceleracao imediata",
        ),
        (
            AVAX_ADJUSTED,
            {
                "adx": 19.44,
                "atr_pct": 0.1376,
                "bb_width": 0.006418,
                "orderbook_pressure": -0.21877,
            },
            "Reload fraco com compressao e book vendedor",
        ),
    ],
)
def test_profile_rule_survives_global_merge_and_blocks(rule, metrics, expected_name):
    effective = merge_profile_runtime_block_config(
        {"block_rules": {"blocks": [rule]}},
        {"block_rules": {"blocks": [_rule("GLOBAL")]}},
    )

    result = BlockEngine(effective["block_rules"]).evaluate(metrics)

    assert result["blocked"] is True
    assert expected_name in result["triggered_blocks"]


@pytest.mark.parametrize(
    ("rule", "metrics"),
    [
        (
            VIRTUAL_ADJUSTED,
            {
                "entry_exhaustion_score": 69.1,
                "rsi_6": 77.72,
                "macd_hist_slope_3": 0.01,
            },
        ),
        (
            ASTER_ADJUSTED,
            {
                "stoch_k": 88,
                "rsi": 68.81,
                "macd_histogram_slope": -0.00006214,
            },
        ),
        (
            AVAX_ADJUSTED,
            {
                "adx": 22,
                "atr_pct": 0.1376,
                "bb_width": 0.006418,
                "orderbook_pressure": -0.21877,
            },
        ),
        (
            AVAX_ADJUSTED,
            {
                "adx": 19.44,
                "atr_pct": 0.1376,
                "bb_width": 0.006418,
                "orderbook_pressure": 0.05,
            },
        ),
    ],
)
def test_profile_confluence_rule_does_not_overblock_when_one_condition_fails(
    rule, metrics
):
    result = BlockEngine({"blocks": [rule]}).evaluate(metrics)

    assert result["blocked"] is False


def test_global_spread_rule_blocks_only_when_spread_exceeds_allowed_maximum():
    spread_rule = {
        "id": "b2",
        "name": "Spread too high",
        "type": "threshold",
        "indicator": "spread_pct",
        "operator": "<",
        "value": 0.3,
        "enabled": True,
    }
    engine = BlockEngine({"blocks": [spread_rule]})

    assert engine.evaluate({"spread_pct": 0.1})["blocked"] is False
    assert engine.evaluate({"spread_pct": 0.3})["blocked"] is True
    assert engine.evaluate({"spread_pct": 0.4})["blocked"] is True


def test_block_engine_emits_condition_level_audit_for_virtual():
    result = BlockEngine({"blocks": [VIRTUAL_ORIGINAL]}).evaluate(
        {"entry_exhaustion_score": 69.1, "rsi_6": 77.72}
    )

    assert result["matched_blocks"] == ["Exaustao curta por RSI"]
    assert result["blocked_by"] == ["Exaustao curta por RSI"]
    audit = result["rules"][0]
    assert audit["matched"] is True
    assert audit["conditions"] == [
        {
            "indicator": "entry_exhaustion_score",
            "left": None,
            "right": None,
            "operator": ">=",
            "expected": 68,
            "actual": 69.1,
            "result": True,
            "status": "PASS",
            "reason_code": None,
        },
        {
            "indicator": "rsi_6",
            "left": None,
            "right": None,
            "operator": ">=",
            "expected": 75,
            "actual": 77.72,
            "result": True,
            "status": "PASS",
            "reason_code": None,
        },
    ]


@pytest.mark.asyncio
async def test_virtual_specific_rule_reaches_operational_pipeline_and_vetoes(
    monkeypatch,
):
    from app.tasks import pipeline_scan

    async def fake_score(assets, **kwargs):
        assets[0]["_score"] = 80
        assets[0]["_score_components"] = {}
        return {"bucketed": 1, "robust_used": 1, "fallbacks": 0}

    monkeypatch.delenv("L3_GATE_V2_OPERATIONAL", raising=False)
    monkeypatch.setattr(
        pipeline_scan,
        "_apply_robust_authoritative_scoring",
        fake_score,
    )
    effective = merge_profile_runtime_block_config(
        {
            "default_timeframe": "5m",
            "block_rules": {"blocks": [VIRTUAL_ORIGINAL]},
            "filters": {"logic": "AND", "conditions": []},
            "signals": {"logic": "AND", "conditions": []},
            "entry_triggers": {"logic": "AND", "conditions": []},
        },
        {"block_rules": {"blocks": [_rule("GLOBAL")]}},
        profile_id="6f9f8a13-603c-4649-93b3-55dcb700dcf8",
        profile_version_id="version-virtual",
    )

    decisions = await pipeline_scan._evaluate_l3_decisions(
        [
            {
                "symbol": "VIRTUAL_USDT",
                "is_futures": False,
                "indicators": {
                    "entry_exhaustion_score": 69.1,
                    "rsi_6": 77.72,
                },
            }
        ],
        effective,
        "L3",
        score_config={},
        profile_id="6f9f8a13-603c-4649-93b3-55dcb700dcf8",
    )

    decision = decisions[0]
    assert decision["decision"] == "BLOCK"
    assert decision["l3_pass"] is False
    assert decision["_processed"]["blocked"] is True
    assert decision["_processed"]["block_reasons"] == [
        "Exaustao curta por RSI"
    ]
    assert decision["metrics"]["block_rules_audit"]["blocked"] is True
    assert decision["metrics"]["block_rules_lineage"][
        "effective_block_rules_hash"
    ]
