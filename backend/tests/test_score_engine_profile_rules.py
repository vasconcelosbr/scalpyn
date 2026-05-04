"""ScoreEngine tests — Phase 4 cleanup.

After Phase 4, ``ScoreEngine.compute_score`` is a thin adapter that
delegates to ``app.services.robust_indicators.compute_asset_score``. The
legacy 4-bucket weighted-total math (and its per-bucket ``components``
breakdown) was removed.

These tests verify:
  * Constructor + config helpers (``merge_score_config`` /
    ``hydrate_profile_scoring`` / ``resolve_rule_category``) still behave
    as before — they are pure config plumbing, not scoring math.
  * ``compute_score`` returns the legacy *response shape* but with the
    bucket sub-scores zeroed out and ``components.engine == "robust"``.
  * ``compute_score`` routes through ``compute_asset_score``: when the
    robust adapter returns a payload, ``total_score`` reflects that
    payload; when it returns ``None`` (e.g. sparse fixtures rejected by
    the critical-indicator gate) the response degrades to ``no_data``
    while still surfacing matched-rule IDs for drilldown.
  * ``get_full_breakdown`` (observability primitive) still reports
    per-rule pass/fail using the rule's indicator condition — unchanged.
"""

import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.api.profiles import _validate_profile_config
from app.services.score_engine import (
    ScoreEngine,
    hydrate_profile_scoring,
    merge_score_config,
    resolve_rule_category,
)


# ── compute_score now routes through compute_asset_score ──────────────────────


def test_compute_score_routes_through_robust_adapter():
    """``ScoreEngine.compute_score`` must call ``compute_asset_score`` and
    surface its score as ``total_score``. The legacy bucket math is gone,
    so ``components.engine`` is the robust tag and the per-bucket sub-scores
    are reported as 0.0."""
    config = {
        "scoring_rules": [
            {"id": "rsi_1", "indicator": "rsi", "operator": "<=",
             "value": 30, "points": 25, "category": "momentum"},
        ],
    }
    engine = ScoreEngine(config)

    fake_payload = {
        "score": 72.5,
        "score_confidence": 0.83,
        "global_confidence": 0.91,
        "matched_rules": [{"rule_id": "rsi_1", "indicator": "rsi"}],
    }
    with patch(
        "app.services.robust_indicators.compute_asset_score",
        return_value=fake_payload,
    ) as mock_score:
        result = engine.compute_score({"rsi": 25, "symbol": "BTC_USDT"})

    assert mock_score.call_count == 1
    assert result["total_score"] == 72.5
    assert result["classification"] == "buy"  # 65 ≤ 72.5 < 80
    # Bucket sub-scores reported as 0 — the robust engine has no per-bucket math.
    assert result["components"]["liquidity_score"] == 0.0
    assert result["components"]["market_structure_score"] == 0.0
    assert result["components"]["momentum_score"] == 0.0
    assert result["components"]["signal_score"] == 0.0
    # Engine breadcrumbs preserved.
    assert result["components"]["engine"] == "robust"
    assert result["components"]["score_confidence"] == 0.83
    assert result["components"]["global_confidence"] == 0.91
    # matched_rules collapsed to rule IDs (drilldown contract).
    assert result["matched_rules"] == ["rsi_1"]
    # category_summaries deliberately empty under the robust adapter.
    assert result["category_summaries"] == {}


def test_compute_score_classification_uses_thresholds():
    """``classification`` is derived from the configured thresholds against
    the robust score, not from the legacy bucket math."""
    engine = ScoreEngine({
        "scoring_rules": [],
        "thresholds": {"strong_buy": 90, "buy": 70, "neutral": 50},
    })

    cases = [
        (95.0, "strong_buy"),
        (75.0, "buy"),
        (60.0, "neutral"),
        (30.0, "avoid"),
    ]
    for score, expected in cases:
        with patch(
            "app.services.robust_indicators.compute_asset_score",
            return_value={
                "score": score,
                "score_confidence": 0.7,
                "global_confidence": 0.8,
                "matched_rules": [],
            },
        ):
            result = engine.compute_score({"rsi": 50, "symbol": "X"})
        assert result["total_score"] == round(score, 2)
        assert result["classification"] == expected, f"score={score}"


def test_compute_score_degrades_to_no_data_when_robust_rejects():
    """When the robust adapter returns ``None`` (critical-indicator gate
    or low confidence), ``compute_score`` returns the legacy response
    shape with ``total_score=0`` and ``classification='no_data'``, but
    still surfaces matched-rule IDs so the drilldown UI has something to
    show."""
    config = {
        "scoring_rules": [
            {"id": "rsi_1", "indicator": "rsi", "operator": "<=",
             "value": 30, "points": 25, "category": "momentum"},
        ],
    }
    engine = ScoreEngine(config)

    with patch(
        "app.services.robust_indicators.compute_asset_score",
        return_value=None,
    ):
        # rsi=25 satisfies the rsi<=30 condition, so the legacy
        # rule-evaluator should still report it as matched even though
        # the robust path declined to score.
        result = engine.compute_score({"rsi": 25, "symbol": "X"})

    assert result["total_score"] == 0.0
    assert result["classification"] == "no_data"
    assert result["components"]["engine"] == "robust"
    assert result["matched_rules"] == ["rsi_1"]


def test_compute_score_empty_indicators_returns_no_data():
    engine = ScoreEngine({"scoring_rules": []})
    result = engine.compute_score({})
    assert result["total_score"] == 0.0
    assert result["classification"] == "no_data"
    assert result["matched_rules"] == []


# ── get_full_breakdown is unchanged (observability primitive) ─────────────────


def test_get_full_breakdown_reports_rule_condition_pass_fail():
    """``get_full_breakdown`` is pure rule-condition evaluation — no
    scoring math. ``points_awarded`` reflects whether the rule's indicator
    condition matched, not its weighted contribution under the robust
    engine."""
    engine = ScoreEngine({
        "scoring_rules": [
            {"id": "rsi_1", "indicator": "rsi", "operator": "<=",
             "value": 30, "points": 25, "category": "momentum"},
            {"id": "macd_1", "indicator": "macd", "operator": ">",
             "value": 0, "points": 10, "category": "momentum"},
        ],
    })
    breakdown = engine.get_full_breakdown({"rsi": 25, "macd": -1})

    by_id = {r["id"]: r for r in breakdown}
    assert by_id["rsi_1"]["passed"] is True
    assert by_id["rsi_1"]["points_awarded"] == 25.0
    assert by_id["macd_1"]["passed"] is False
    assert by_id["macd_1"]["points_awarded"] == 0.0
    # Category mapping still resolved correctly.
    assert by_id["rsi_1"]["category"] == "momentum"


# ── Config helpers — unchanged behavior ───────────────────────────────────────


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
    ``"liquidity"``."""
    assert resolve_rule_category({"indicator": "taker_ratio"}) == "liquidity"
    # Sanity: explicit category still wins over the default mapping.
    assert (
        resolve_rule_category({"indicator": "taker_ratio", "category": "signal"})
        == "signal"
    )
    # Sanity: buy_pressure (the alias field) sits in the same bucket.
    assert resolve_rule_category({"indicator": "buy_pressure"}) == "liquidity"


# ── Task #211: get_full_breakdown enriches matched rules with awarded_points ──


def test_get_full_breakdown_enriches_matched_rules_with_awarded_points():
    """Task #211 — scoring is deterministic: ``awarded_points`` equals the
    full configured ``points`` for matched rules (no confidence weighting).
    ``get_full_breakdown`` must mesh the engine's matched_rules onto the
    per-rule list, attaching ``awarded_points``, ``indicator_confidence``,
    and ``data_available`` only to matched entries. Non-matched rules keep
    the nominal-only shape so the UI fallback path keeps working."""
    config = {
        "scoring_rules": [
            {"id": "rsi_low",  "indicator": "rsi", "operator": "<=", "value": 40, "points": 20, "category": "momentum"},
            {"id": "rsi_high", "indicator": "rsi", "operator": ">=", "value": 70, "points": 15, "category": "momentum"},
            {"id": "vol",      "indicator": "volume_spike", "operator": ">=", "value": 2, "points": 10, "category": "liquidity"},
        ],
    }
    engine = ScoreEngine(config)

    fake_payload = {
        "score": 66.67,
        "score_confidence": 0.36,
        "global_confidence": 0.7,
        "matched_rules": [
            {"rule_id": "rsi_low",  "indicator": "rsi", "operator": "<=", "value": 40,
             "points": 20.0, "awarded_points": 20.0, "confidence": 0.42, "data_available": True, "category": "momentum"},
            {"rule_id": "vol", "indicator": "volume_spike", "operator": ">=", "value": 2,
             "points": 10.0, "awarded_points": 10.0, "confidence": 0.30, "data_available": True, "category": "liquidity"},
        ],
    }
    with patch(
        "app.services.robust_indicators.compute_asset_score",
        return_value=fake_payload,
    ):
        breakdown = engine.get_full_breakdown(
            {"rsi": 25, "volume_spike": 3.5, "symbol": "HYPE_USDT"}
        )

    by_id = {r["id"]: r for r in breakdown}

    assert by_id["rsi_low"]["awarded_points"] == 20.0
    assert by_id["rsi_low"]["indicator_confidence"] == 0.42
    assert by_id["rsi_low"]["data_available"] is True
    assert by_id["vol"]["awarded_points"] == 10.0
    assert by_id["vol"]["indicator_confidence"] == 0.30

    assert "awarded_points" not in by_id["rsi_high"]
    assert "indicator_confidence" not in by_id["rsi_high"]

    matched = [r for r in breakdown if "awarded_points" in r]
    awarded_total = sum(r["awarded_points"] for r in matched)
    denom = sum(r["points_possible"] for r in breakdown if (r.get("type") or "positive") != "penalty")
    reconstructed_score = (awarded_total / denom) * 100
    assert abs(reconstructed_score - fake_payload["score"]) < 0.05, (
        f"reconciliation drift: reconstructed={reconstructed_score:.4f} "
        f"vs payload.score={fake_payload['score']}"
    )


def test_get_full_breakdown_reuses_caller_supplied_payload_without_recomputing():
    """Hot callers (pipeline refresh, watchlist trace) that already
    invoked ``compute_asset_score`` for an asset can pass the payload
    via ``score_payload=`` to skip the redundant robust call. Verifies
    the optimization actually short-circuits (mock asserts 0 calls)."""
    config = {
        "scoring_rules": [
            {"id": "rsi_low", "indicator": "rsi", "operator": "<=", "value": 40, "points": 20, "category": "momentum"},
        ],
    }
    engine = ScoreEngine(config)

    pre_computed = {
        "score": 100.0,
        "matched_rules": [
            {"rule_id": "rsi_low", "indicator": "rsi", "points": 20.0,
             "awarded_points": 20.0, "confidence": 0.42, "data_available": True},
        ],
    }
    with patch(
        "app.services.robust_indicators.compute_asset_score",
    ) as mock_score:
        breakdown = engine.get_full_breakdown(
            {"rsi": 25}, score_payload=pre_computed,
        )

    assert mock_score.call_count == 0
    rule = breakdown[0]
    assert rule["awarded_points"] == 20.0
    assert rule["indicator_confidence"] == 0.42
    assert rule["data_available"] is True


def test_get_full_breakdown_falls_back_to_nominal_when_robust_rejects():
    """When ``compute_asset_score`` returns ``None`` (critical-gate or
    confidence-gate rejection, e.g. sparse fixtures), every rule must
    render in legacy mode — no ``awarded_points`` keys — so the UI
    falls back to nominal points and shows the "(legacy)" marker."""
    config = {
        "scoring_rules": [
            {"id": "rsi_low", "indicator": "rsi", "operator": "<=", "value": 40, "points": 20, "category": "momentum"},
        ],
    }
    engine = ScoreEngine(config)

    with patch(
        "app.services.robust_indicators.compute_asset_score",
        return_value=None,
    ):
        breakdown = engine.get_full_breakdown({"rsi": 25})

    rule = breakdown[0]
    assert rule["passed"] is True
    assert rule["points_awarded"] == 20.0
    assert "awarded_points" not in rule
    assert "indicator_confidence" not in rule


def test_taker_ratio_rule_drilldown_reports_liquidity_category():
    """End-to-end drilldown check: a default-category taker_ratio rule
    reports ``category="liquidity"`` (not ``signal``) in
    ``get_full_breakdown``. The points_awarded reflects rule pass/fail,
    not legacy bucket scoring."""
    config = {
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
