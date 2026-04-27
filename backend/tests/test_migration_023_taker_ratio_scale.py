"""Unit tests for migration 023 helper functions.

The migration itself runs against PostgreSQL JSONB and is exercised by
the alembic test harness; here we lock down the *pure* helpers that do
the per-row rewriting, so that future scale changes don't accidentally
change the conversion math or break idempotency.
"""

import importlib.util
import os
import sys

import pytest


def _load_migration_module():
    here = os.path.dirname(__file__)
    path = os.path.abspath(
        os.path.join(
            here,
            "..",
            "alembic",
            "versions",
            "023_taker_ratio_scale_v2.py",
        )
    )
    spec = importlib.util.spec_from_file_location("migration_023", path)
    mod = importlib.util.module_from_spec(spec)
    # The migration imports `from alembic import op` at module top, but
    # we never call upgrade()/downgrade() in these tests, so we stub
    # those modules out cheaply if absent.
    sys.modules.setdefault("migration_023", mod)
    spec.loader.exec_module(mod)
    return mod


migration = _load_migration_module()


# ── _convert_threshold ─────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "old, expected",
    [
        (0.0, 0.0),         # all sells → 0/(0+1) = 0
        (1.0, 0.5),         # equilibrium on the legacy scale → 0.5 on the new one
        (1.04, 0.5098),     # "sellers slightly dominant" preserved
        (1.20, 0.5455),
        (1.50, 0.6),
        (2.00, 0.6667),
        (4.00, 0.8),
    ],
)
def test_convert_threshold_known_anchors(old, expected):
    assert migration._convert_threshold(old) == expected


def test_convert_threshold_handles_string_numbers():
    # ProfileBuilder occasionally serialises numbers as strings.
    assert migration._convert_threshold("1.5") == 0.6


def test_convert_threshold_passes_through_non_numeric():
    assert migration._convert_threshold(None) is None
    assert migration._convert_threshold("not a number") == "not a number"
    assert migration._convert_threshold(True) is True  # bool short-circuit
    assert migration._convert_threshold(False) is False


def test_convert_threshold_passes_through_negative():
    # Negative thresholds are nonsensical for taker_ratio; leave them
    # alone so the user notices and edits manually.
    assert migration._convert_threshold(-1.5) == -1.5


def test_convert_threshold_lists_are_mapped_elementwise():
    assert migration._convert_threshold([1.0, 2.0]) == [0.5, 0.6667]


# ── _migrate_condition / _migrate_block_rules / _migrate_filters ──────────────

def test_migrate_block_rules_rewrites_taker_ratio_thresholds():
    block_rules = {
        "blocks": [
            {
                "id": "b1",
                "conditions": [
                    {"indicator": "taker_ratio", "operator": "<", "value": 1.04},
                    # Other indicators are left untouched.
                    {"indicator": "rsi", "operator": ">", "value": 70},
                ],
            }
        ]
    }
    changed = migration._migrate_block_rules(block_rules)
    assert changed is True
    conds = block_rules["blocks"][0]["conditions"]
    assert conds[0] == {"indicator": "taker_ratio", "operator": "<", "value": 0.5098}
    assert conds[1] == {"indicator": "rsi", "operator": ">", "value": 70}


def test_migrate_filters_handles_both_field_and_indicator_keys():
    filters = {
        "conditions": [
            {"field": "taker_ratio", "operator": ">", "value": 1.5},
            {"indicator": "taker_ratio", "operator": ">=", "value": 2.0},
            {"field": "rsi", "operator": "<", "value": 30},
        ]
    }
    changed = migration._migrate_filters(filters)
    assert changed is True
    assert filters["conditions"][0]["value"] == 0.6
    assert filters["conditions"][1]["value"] == 0.6667
    assert filters["conditions"][2]["value"] == 30


def test_migrate_condition_skips_unsupported_operator():
    cond = {"indicator": "taker_ratio", "operator": "in", "value": [1.0, 2.0]}
    assert migration._migrate_condition(cond, "indicator") is False
    # Untouched, because ``in`` is not in the convertible set.
    assert cond["value"] == [1.0, 2.0]


def test_migrate_condition_handles_between_with_min_max_keys():
    """``between`` conditions in this codebase use separate ``min`` and
    ``max`` keys (see ``rule_engine._apply_operator``), not a list under
    ``value``. Both bounds must be converted independently — the mapping
    f(x)=x/(x+1) is strictly increasing so ``min <= x <= max`` is
    preserved on the new scale."""
    cond = {
        "indicator": "taker_ratio",
        "operator": "between",
        "min": 0.5,
        "max": 1.5,
    }
    assert migration._migrate_condition(cond, "indicator") is True
    # 0.5 / 1.5 = 0.3333; 1.5 / 2.5 = 0.6.
    assert cond["min"] == 0.3333
    assert cond["max"] == 0.6
    # Order is preserved (still a valid range on the new scale).
    assert cond["min"] < cond["max"]


def test_migrate_condition_between_with_value_list_also_works():
    """Some legacy rows store ``between`` bounds as ``value: [min, max]``
    instead of separate keys. _convert_threshold maps lists element-wise."""
    cond = {
        "indicator": "taker_ratio",
        "operator": "between",
        "value": [0.5, 1.5],
    }
    assert migration._migrate_condition(cond, "indicator") is True
    assert cond["value"] == [0.3333, 0.6]


def test_migrate_condition_partial_bounds_only_converts_present_keys():
    """A condition that only has a ``min`` (or only a ``max``) bound must
    still convert that one without crashing on the missing key."""
    cond = {
        "indicator": "taker_ratio",
        "operator": "between",
        "min": 1.5,
    }
    assert migration._migrate_condition(cond, "indicator") is True
    assert cond["min"] == 0.6
    assert "max" not in cond


# ── _migrate_profile_config (idempotency) ─────────────────────────────────────

def test_migrate_profile_config_marks_and_returns_new_config():
    cfg = {
        "block_rules": {
            "blocks": [
                {
                    "conditions": [
                        {"indicator": "taker_ratio", "operator": "<", "value": 1.04}
                    ]
                }
            ]
        }
    }
    new_cfg, changed = migration._migrate_profile_config(cfg)
    assert changed is True
    assert new_cfg is not None
    assert new_cfg["_taker_ratio_scale_v2"] is True
    assert (
        new_cfg["block_rules"]["blocks"][0]["conditions"][0]["value"] == 0.5098
    )


def test_migrate_profile_config_is_idempotent():
    """Calling twice on the same config does not double-convert."""
    cfg = {
        "block_rules": {
            "blocks": [
                {
                    "conditions": [
                        {"indicator": "taker_ratio", "operator": "<", "value": 1.04}
                    ]
                }
            ]
        }
    }
    new_cfg, _ = migration._migrate_profile_config(cfg)
    # Second pass: the marker is set, so the helper short-circuits.
    new_cfg2, changed2 = migration._migrate_profile_config(new_cfg)
    assert new_cfg2 is None
    assert changed2 is False
    # And the value is still the once-converted 0.5098, not 0.3398.
    assert (
        new_cfg["block_rules"]["blocks"][0]["conditions"][0]["value"] == 0.5098
    )


def test_migrate_profile_config_marks_even_when_no_taker_ratio_rule():
    """Profiles with no taker_ratio rule still get the marker so that
    the idempotency check works uniformly across every row."""
    cfg = {
        "block_rules": {
            "blocks": [
                {"conditions": [{"indicator": "rsi", "operator": ">", "value": 70}]}
            ]
        }
    }
    new_cfg, changed = migration._migrate_profile_config(cfg)
    assert changed is False
    assert new_cfg is not None
    assert new_cfg["_taker_ratio_scale_v2"] is True


def test_migrate_profile_config_handles_missing_keys():
    # Empty config — no blocks, no filters. Just gets the marker.
    cfg = {}
    new_cfg, changed = migration._migrate_profile_config(cfg)
    assert changed is False
    assert new_cfg == {"_taker_ratio_scale_v2": True}
