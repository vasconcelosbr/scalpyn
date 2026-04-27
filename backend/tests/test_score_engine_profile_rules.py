import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.api.profiles import _validate_profile_config
from app.services.score_engine import ScoreEngine, hydrate_profile_scoring, merge_score_config


def test_score_engine_uses_rule_level_category_from_config():
    config = {
        "weights": {"liquidity": 25, "market_structure": 25, "momentum": 25, "signal": 25},
        "scoring_rules": [
            {
                "id": "rsi_liq",
                "indicator": "rsi",
                "operator": "<=",
                "value": 30,
                "points": 25,
                "category": "liquidity",
            }
        ],
    }

    engine = ScoreEngine(config)
    result = engine.compute_alpha_score({"rsi": 25})
    breakdown = engine.get_full_breakdown({"rsi": 25})

    assert result["total_score"] == 100
    assert result["components"]["liquidity_score"] == 100
    assert result["components"]["momentum_score"] == 0
    assert breakdown[0]["category"] == "liquidity"


def test_score_engine_normalizes_points_within_category():
    config = {
        "weights": {"liquidity": 0, "market_structure": 0, "momentum": 100, "signal": 0},
        "scoring_rules": [
            {"id": "rsi_1", "indicator": "rsi", "operator": "<=", "value": 30, "points": 10, "category": "momentum"},
            {"id": "macd_1", "indicator": "macd", "operator": ">", "value": 0, "points": 10, "category": "momentum"},
        ],
    }

    engine = ScoreEngine(config)
    result = engine.compute_alpha_score({"rsi": 25, "macd": -1})

    assert result["components"]["momentum_score"] == 50
    assert result["total_score"] == 50
    assert result["classification"] == "neutral"


def test_score_engine_ignores_inactive_categories_in_weight_denominator():
    config = {
        "weights": {"liquidity": 25, "market_structure": 25, "momentum": 25, "signal": 25},
        "scoring_rules": [
            {"id": "rsi_1", "indicator": "rsi", "operator": "<=", "value": 30, "points": 10, "category": "momentum"},
        ],
    }

    engine = ScoreEngine(config)
    result = engine.compute_alpha_score({"rsi": 25})

    assert result["components"]["momentum_score"] == 100
    assert result["total_score"] == 100
    assert result["classification"] == "strong_buy"


def test_merge_score_config_respects_profile_selected_rule_ids():
    global_config = {
        "weights": {"liquidity": 25, "market_structure": 25, "momentum": 25, "signal": 25},
        "scoring_rules": [
            {"id": "rsi_1", "indicator": "rsi", "operator": "<=", "value": 30, "points": 30, "category": "momentum"},
            {"id": "adx_1", "indicator": "adx", "operator": ">=", "value": 20, "points": 20, "category": "market_structure"},
        ],
    }
    profile_config = {
        "filters": {
            "conditions": [
                {"field": "rsi", "rule_id": "rsi_1"},
            ]
        },
        "scoring": {
            "enabled": True,
            "weights": {"liquidity": 10, "market_structure": 40, "momentum": 30, "signal": 20},
        },
    }

    merged = merge_score_config(global_config, profile_config)

    assert [rule["id"] for rule in merged["scoring_rules"]] == ["rsi_1"]
    assert merged["weights"] == profile_config["scoring"]["weights"]


def test_hydrate_profile_scoring_injects_global_rules_into_profile():
    global_config = {
        "weights": {"liquidity": 25, "market_structure": 25, "momentum": 25, "signal": 25},
        "scoring_rules": [
            {"id": "rsi_1", "indicator": "rsi", "operator": "<=", "value": 30, "points": 30, "category": "momentum"},
        ],
        "thresholds": {"strong_buy": 80, "buy": 65, "neutral": 40},
    }
    profile_config = {
        "filters": {"conditions": [{"field": "rsi", "rule_id": "rsi_1"}]},
        "scoring": {
            "enabled": False,
            "weights": {"liquidity": 30, "market_structure": 20, "momentum": 30, "signal": 20},
        },
    }

    hydrated = hydrate_profile_scoring(profile_config, global_config)

    assert hydrated["scoring"]["enabled"] is False
    assert [rule["id"] for rule in hydrated["scoring"]["rules"]] == ["rsi_1"]
    assert hydrated["scoring"]["thresholds"] == global_config["thresholds"]


def test_validate_profile_config_preserves_scoring_enabled_toggle():
    validated = _validate_profile_config({"scoring": {"enabled": False}})

    assert validated["scoring"]["enabled"] is False


def test_validate_profile_config_preserves_default_timeframe():
    validated = _validate_profile_config({"default_timeframe": "15m"})

    assert validated["default_timeframe"] == "15m"


# ── Category mapping regression (#82) ──────────────────────────────────────────


def test_resolve_rule_category_taker_ratio_defaults_to_liquidity():
    """Regression for the duplicate-key bug fixed in #82: ``taker_ratio``
    used to appear twice in ``_IND_CATEGORY`` (liquidity then signal),
    so dict resolution silently kept the legacy "signal" mapping. After
    the fix, a rule with no explicit ``category`` must resolve to
    ``"liquidity"`` (since the indicator now ranges in [0, 1] with
    equilibrium 0.5, like buy_pressure)."""
    from app.services.score_engine import resolve_rule_category

    assert resolve_rule_category({"indicator": "taker_ratio"}) == "liquidity"
    # Sanity: explicit category still wins over the default mapping.
    assert (
        resolve_rule_category({"indicator": "taker_ratio", "category": "signal"})
        == "signal"
    )
    # Sanity: buy_pressure (the alias field) sits in the same bucket.
    assert resolve_rule_category({"indicator": "buy_pressure"}) == "liquidity"


def test_taker_ratio_rule_contributes_to_liquidity_bucket():
    """End-to-end check: a default-category taker_ratio rule reports
    ``category="liquidity"`` in the breakdown (not ``signal``) and feeds
    the liquidity component score, not the signal one."""
    config = {
        "weights": {
            "liquidity": 50,
            "market_structure": 0,
            "momentum": 0,
            "signal": 50,
        },
        "scoring_rules": [
            {
                "id": "tr_buy_dom",
                "indicator": "taker_ratio",
                "operator": ">=",
                "value": 0.55,
                "points": 100,
                # Note: NO explicit "category" — must default to liquidity.
            }
        ],
    }

    engine = ScoreEngine(config)
    breakdown = engine.get_full_breakdown({"taker_ratio": 0.7})
    rule = next(r for r in breakdown if r["id"] == "tr_buy_dom")
    assert rule["category"] == "liquidity"
    assert rule["passed"] is True
    assert rule["points_awarded"] == 100.0

    # And the rule's points must land in the liquidity component, not signal.
    score = engine.compute_alpha_score({"taker_ratio": 0.7})
    assert score["components"]["liquidity_score"] == 100
    assert score["components"]["signal_score"] == 0
