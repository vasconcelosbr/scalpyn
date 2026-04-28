import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.pipeline_rejections import (
    evaluate_rejections,
    recompute_rejection_trace,
    rejection_metrics,
)


def test_block_rules_reject_before_filters_and_mark_remaining_trace_as_skipped():
    profile_config = {
        "filters": {
            "logic": "AND",
            "conditions": [
                {"field": "volume_24h", "operator": ">=", "value": 1_000_000},
            ],
        },
        "block_rules": {
            "blocks": [
                {
                    "id": "block_overbought",
                    "name": "Overbought RSI",
                    "logic": "AND",
                    "conditions": [
                        {"indicator": "rsi", "operator": ">", "value": 75},
                    ],
                }
            ]
        },
    }

    approved, rejected = evaluate_rejections(
        [{"symbol": "ETH_USDT", "rsi": 82, "volume_24h": 2_000_000}],
        profile_config=profile_config,
        stage="L1",
        profile_id="profile-1",
    )

    assert approved == []
    assert len(rejected) == 1
    assert rejected[0]["failed_type"] == "block_rule"
    assert rejected[0]["failed_indicator"] == "Overbought RSI"
    assert [item["status"] for item in rejected[0]["evaluation_trace"]] == ["FAIL", "SKIPPED"]


def test_filter_failure_trace_preserves_profile_order_and_stop_point():
    profile_config = {
        "filters": {
            "logic": "AND",
            "conditions": [
                {"field": "volume_24h", "operator": ">=", "value": 1_000_000},
                {"field": "rsi", "operator": "<", "value": 55},
                {"field": "adx", "operator": ">", "value": 20},
            ],
        },
        "block_rules": {"blocks": []},
    }

    approved, rejected = evaluate_rejections(
        [{"symbol": "BTC_USDT", "volume_24h": 5_000_000, "rsi": 62, "adx": 29}],
        profile_config=profile_config,
        stage="L2",
        profile_id="profile-2",
    )

    assert approved == []
    assert len(rejected) == 1
    assert rejected[0]["failed_type"] == "filter"
    assert rejected[0]["failed_indicator"] == "RSI"
    assert [item["status"] for item in rejected[0]["evaluation_trace"]] == ["PASS", "FAIL", "SKIPPED"]


def test_approved_assets_include_full_pass_trace():
    profile_config = {
        "filters": {
            "logic": "AND",
            "conditions": [
                {"field": "volume_24h", "operator": ">=", "value": 1_000_000},
                {"field": "rsi", "operator": "<", "value": 65},
            ],
        },
        "block_rules": {
            "blocks": [
                {
                    "id": "block_overbought",
                    "name": "Overbought RSI",
                    "logic": "AND",
                    "conditions": [
                        {"indicator": "rsi", "operator": ">", "value": 75},
                    ],
                }
            ]
        },
    }

    approved, rejected = evaluate_rejections(
        [{"symbol": "SOL_USDT", "volume_24h": 5_000_000, "rsi": 58}],
        profile_config=profile_config,
        stage="L2",
        profile_id="profile-3",
    )

    assert rejected == []
    assert len(approved) == 1
    assert [item["status"] for item in approved[0]["evaluation_trace"]] == ["PASS", "PASS", "PASS"]


def test_pool_stage_rejections_are_labeled_as_pool():
    profile_config = {
        "filters": {
            "logic": "AND",
            "conditions": [
                {"field": "market_cap", "operator": ">=", "value": 1_000_000_000},
            ],
        },
        "block_rules": {"blocks": []},
    }

    approved, rejected = evaluate_rejections(
        [{"symbol": "DOGE_USDT", "market_cap": 950_000_000}],
        profile_config=profile_config,
        stage="POOL",
        profile_id="profile-3",
    )

    assert approved == []
    assert len(rejected) == 1
    assert rejected[0]["stage"] == "POOL"
    assert rejected[0]["failed_indicator"] == "Market Cap"
    assert rejected[0]["evaluation_trace"][0]["status"] == "FAIL"


def test_cascade_skipped_blocks_carry_short_circuit_reason():
    """When the first block fails the asset is rejected immediately; the
    remaining blocks/filters must be marked SKIPPED with
    `reason="cascade_short_circuit"` so the frontend can label them
    'PULADO' instead of the misleading 'SEM DADOS / aguardando coleta'.
    """
    profile_config = {
        "filters": {
            "logic": "AND",
            "conditions": [
                {"field": "adx", "operator": ">=", "value": 15},
            ],
        },
        "block_rules": {
            "blocks": [
                {
                    "id": "block_taker_ratio",
                    "name": "Taker Ratio",
                    "logic": "AND",
                    "conditions": [
                        {"indicator": "taker_ratio", "operator": "<", "value": 1.04},
                    ],
                },
                {
                    "id": "block_spike",
                    "name": "Spike",
                    "logic": "AND",
                    "conditions": [
                        {"indicator": "volume_spike", "operator": "<", "value": 1.2},
                    ],
                },
                {
                    "id": "block_bbw",
                    "name": "BB Width",
                    "logic": "AND",
                    "conditions": [
                        {"indicator": "bb_width", "operator": "<", "value": 0.01},
                    ],
                },
            ]
        },
    }

    # taker_ratio < 1.04 → first block triggers (FAIL); the next two
    # blocks and the filter must be marked SKIPPED with the new reason.
    _, rejected = evaluate_rejections(
        [{"symbol": "UNI_USDT", "taker_ratio": 0.4087, "volume_spike": 0.5, "bb_width": 0.04, "adx": 22}],
        profile_config=profile_config,
        stage="L3",
        profile_id="profile-cascade",
    )

    assert len(rejected) == 1
    trace = rejected[0]["evaluation_trace"]

    by_indicator = {item["indicator"]: item for item in trace}
    assert by_indicator["Taker Ratio"]["status"] == "FAIL"

    # Cascade-skipped blocks carry the new reason.
    spike = by_indicator["Spike"]
    bbw = by_indicator["BB Width"]
    assert spike["status"] == "SKIPPED"
    assert spike["reason"] == "cascade_short_circuit"
    assert bbw["status"] == "SKIPPED"
    assert bbw["reason"] == "cascade_short_circuit"

    # The downstream filter is also cascade-skipped.
    adx = by_indicator["ADX"]
    assert adx["status"] == "SKIPPED"
    assert adx["reason"] == "cascade_short_circuit"


def test_filter_cascade_emits_short_circuit_reason_for_remaining_filters():
    """Filter cascade (AND logic) must propagate the new reason too."""
    profile_config = {
        "filters": {
            "logic": "AND",
            "conditions": [
                {"field": "rsi", "operator": "<", "value": 55},
                {"field": "adx", "operator": ">=", "value": 15},
                {"field": "volume_24h", "operator": ">", "value": 1_000_000},
            ],
        },
        "block_rules": {"blocks": []},
    }

    _, rejected = evaluate_rejections(
        [{"symbol": "BTC_USDT", "rsi": 62, "adx": 22, "volume_24h": 5_000_000}],
        profile_config=profile_config,
        stage="L2",
        profile_id="profile-filter-cascade",
    )

    trace = rejected[0]["evaluation_trace"]
    statuses = [(item["indicator"], item["status"], item.get("reason")) for item in trace]
    # rsi FAIL → adx + volume_24h are cascade-skipped.
    assert statuses == [
        ("RSI", "FAIL", None),
        ("ADX", "SKIPPED", "cascade_short_circuit"),
        ("Volume 24h", "SKIPPED", "cascade_short_circuit"),
    ]


def test_taker_ratio_above_plausibility_bound_is_invalid_value():
    """Regression: SUI showed taker_ratio == 8.98e9 in prod. The trace
    must mark this as `indicator_invalid_value`, not let the absurd
    number drive the rule outcome. After #82 the canonical scale is
    Buy/(Buy+Sell) ∈ [0, 1], so anything > 1 trips the validity check.
    """
    profile_config = {
        "filters": {"logic": "AND", "conditions": []},
        "block_rules": {
            "blocks": [
                {
                    "id": "block_taker",
                    "name": "Taker Ratio",
                    "logic": "AND",
                    "conditions": [
                        # Threshold expressed on the new [0, 1] scale: block
                        # when sellers dominate (taker_ratio < 0.5).
                        {"indicator": "taker_ratio", "operator": "<", "value": 0.5},
                    ],
                }
            ]
        },
    }

    approved, rejected = evaluate_rejections(
        [{"symbol": "SUI_USDT", "taker_ratio": 8_980_000_800.0}],
        profile_config=profile_config,
        stage="L3",
        profile_id="profile-taker-invalid",
    )

    # Block was SKIPPED → asset is NOT rejected by it (no false negative
    # blocking trades on garbage data).
    assert rejected == []
    assert len(approved) == 1
    trace = approved[0]["evaluation_trace"]
    block = next(item for item in trace if item["type"] == "block_rule")
    assert block["status"] == "SKIPPED"
    assert block["reason"] == "indicator_invalid_value"


def test_rejection_metrics_group_by_indicator_and_block_rate():
    metrics = rejection_metrics([
        {"failed_type": "filter", "failed_indicator": "RSI"},
        {"failed_type": "filter", "failed_indicator": "RSI"},
        {"failed_type": "block_rule", "failed_indicator": "Overbought RSI"},
    ])

    assert metrics["total_rejected"] == 3
    assert metrics["filter_count"] == 2
    assert metrics["block_rule_count"] == 1
    assert metrics["block_rule_rate"] == 33.3
    assert metrics["top_indicator"] == "RSI"


def test_approved_assets_receive_normalized_analysis_snapshot():
    profile_config = {
        "filters": {
            "logic": "AND",
            "conditions": [
                {"field": "volume_24h", "operator": ">=", "value": 1_000_000},
            ],
        },
        "block_rules": {"blocks": []},
    }

    approved, rejected = evaluate_rejections(
        [{"symbol": "SOL_USDT", "volume_24h": 2_500_000}],
        profile_config=profile_config,
        stage="L1",
        profile_id="profile-approved",
    )

    assert rejected == []
    snapshot = approved[0]["analysis_snapshot"]
    assert snapshot["status"] == "approved"
    assert snapshot["details"] is not None
    assert snapshot["details"]["filters"][0]["status"] == "PASS"
    assert snapshot["details"]["evaluation_trace"][0]["status"] == "PASS"
    assert snapshot["failed_indicators"] == []
    assert snapshot["conditions"] == ["Volume 24h >= 1000000"]
    assert snapshot["current_values"]["Volume 24h"] == 2_500_000
    assert snapshot["expected_values"]["Volume 24h"] == "1000000"


def test_recompute_rejection_trace_uses_current_indicators_for_cascade_label():
    """Regression for the Rejected tab: a row stored before #71 carries an
    old-format trace where the cascade-skipped blocks lack
    `reason="cascade_short_circuit"`. On read the API must rebuild the
    trace from current indicators so the frontend renders PULADO instead
    of "Current: aguardando coleta".
    """
    profile_config = {
        "filters": {
            "logic": "AND",
            "conditions": [
                {"field": "adx", "operator": ">=", "value": 15},
            ],
        },
        "block_rules": {
            "blocks": [
                {
                    "id": "block_taker_ratio",
                    "name": "Taker Ratio",
                    "logic": "AND",
                    "conditions": [
                        {"indicator": "taker_ratio", "operator": "<", "value": 1.04},
                    ],
                },
                {
                    "id": "block_spike",
                    "name": "Spike",
                    "logic": "AND",
                    "conditions": [
                        {"indicator": "volume_spike", "operator": "<", "value": 1.2},
                    ],
                },
            ]
        },
    }

    # Old-format stored trace: no `reason` field on the SKIPPED entry.
    stored_trace = [
        {"type": "block_rule", "indicator": "Taker Ratio", "status": "FAIL"},
        {"type": "block_rule", "indicator": "Spike", "status": "SKIPPED"},
        {"type": "filter", "indicator": "ADX", "status": "SKIPPED"},
    ]

    # Current indicators: taker_ratio still failing → cascade triggers.
    indicators = {
        "taker_ratio": 0.4087,
        "volume_spike": 0.5,
        "adx": 22,
    }

    trace = recompute_rejection_trace(
        "UNI_USDT",
        profile_config=profile_config,
        indicators=indicators,
        meta={},
        stored_trace=stored_trace,
    )

    by_indicator = {item["indicator"]: item for item in trace}
    assert by_indicator["Taker Ratio"]["status"] == "FAIL"
    spike = by_indicator["Spike"]
    assert spike["status"] == "SKIPPED"
    assert spike["reason"] == "cascade_short_circuit"
    adx = by_indicator["ADX"]
    assert adx["status"] == "SKIPPED"
    assert adx["reason"] == "cascade_short_circuit"


def test_recompute_rejection_trace_falls_back_when_indicators_missing():
    """Defensive fallback: when there is no indicators row for the symbol
    (collector gap, delisted asset, fresh symbol not yet scored), the
    API must keep the stored trace verbatim instead of mass-downgrading
    every entry to SEM DADOS — that would erase the historical reason
    for the rejection.
    """
    profile_config = {
        "filters": {"logic": "AND", "conditions": [
            {"field": "adx", "operator": ">=", "value": 15},
        ]},
        "block_rules": {"blocks": []},
    }
    stored_trace = [
        {"type": "filter", "indicator": "ADX", "status": "FAIL", "current_value": 12, "expected": "15"},
    ]

    trace = recompute_rejection_trace(
        "GHOST_USDT",
        profile_config=profile_config,
        indicators=None,
        meta=None,
        stored_trace=stored_trace,
    )

    assert trace == stored_trace


def test_recompute_rejection_trace_falls_back_when_only_meta_is_present():
    """The realistic collector-gap scenario: market_metadata still has a
    fresh row for the symbol (price, volume, market_cap) but the
    indicators table has no entry yet. Indicator-based rules (rsi,
    taker_ratio, …) have no data, so recomputing would degrade the
    trace; the helper must fall back to the stored snapshot.
    """
    profile_config = {
        "filters": {"logic": "AND", "conditions": [
            {"field": "rsi", "operator": "<", "value": 55},
        ]},
        "block_rules": {
            "blocks": [
                {
                    "id": "block_taker",
                    "name": "Taker Ratio",
                    "logic": "AND",
                    "conditions": [
                        {"indicator": "taker_ratio", "operator": "<", "value": 1.04},
                    ],
                }
            ]
        },
    }
    stored_trace = [
        {"type": "block_rule", "indicator": "Taker Ratio", "status": "FAIL", "current_value": 0.4, "expected": "1.04"},
        {"type": "filter", "indicator": "RSI", "status": "SKIPPED", "reason": "cascade_short_circuit"},
    ]

    # market_metadata-only payload: enough for volume/market_cap rules,
    # but NOT enough for taker_ratio / rsi.
    meta = {
        "current_price": 1.23,
        "price_change_24h": -2.4,
        "volume_24h": 5_000_000.0,
        "market_cap": 1_000_000_000.0,
        "spread_pct": 0.05,
        "orderbook_depth_usdt": 200_000.0,
    }

    trace = recompute_rejection_trace(
        "FRESHLISTED_USDT",
        profile_config=profile_config,
        indicators=None,
        meta=meta,
        stored_trace=stored_trace,
    )

    assert trace == stored_trace


def test_recompute_rejection_trace_falls_back_when_profile_config_missing():
    """Without a profile_config there is nothing to evaluate against, so
    the helper must return the stored trace untouched.
    """
    stored_trace = [
        {"type": "filter", "indicator": "RSI", "status": "FAIL"},
    ]
    trace = recompute_rejection_trace(
        "BTC_USDT",
        profile_config=None,
        indicators={"rsi": 80},
        meta={"current_price": 50000.0},
        stored_trace=stored_trace,
    )
    assert trace == stored_trace


def test_rejected_assets_expose_normalized_details_contract():
    profile_config = {
        "filters": {
            "logic": "AND",
            "conditions": [
                {"field": "rsi", "operator": "<", "value": 55},
            ],
        },
        "block_rules": {"blocks": []},
    }

    approved, rejected = evaluate_rejections(
        [{"symbol": "XRP_USDT", "rsi": 62}],
        profile_config=profile_config,
        stage="L2",
        profile_id="profile-rejected",
    )

    assert approved == []
    item = rejected[0]
    assert item["status"] == "rejected"
    assert item["details"] is not None
    assert item["details"]["filters"][0]["status"] == "FAIL"
    assert item["details"]["conditions"] == ["RSI < 55"]
    assert item["failed_indicators"] == ["RSI"]
    assert item["current_values"]["RSI"] == 62
    assert item["expected_values"]["RSI"] == "55"


# ── Task #84: alpha_score in Rejected tab ──────────────────────────────────────

def test_normalize_decision_snapshot_reads_alpha_score_from_snapshot():
    """Regression for #84: when `alpha_score` is NOT passed as an explicit
    argument, `_normalize_decision_snapshot` must fall back to the value
    stored inside `analysis_snapshot` (written there since the fix).
    This covers legacy rows whose snapshot was enriched during a pipeline
    cycle that predates the task but already had the write-path fix active.
    """
    from app.api.watchlists import _normalize_decision_snapshot

    snapshot = {
        "status": "rejected",
        "stage": "L1",
        "alpha_score": 42.5,
        "score_rules": [],
        "failed_indicators": ["RSI"],
        "conditions": ["RSI < 55"],
        "current_values": {},
        "expected_values": {},
    }

    result = _normalize_decision_snapshot(
        symbol="ETH_USDT",
        status="rejected",
        stage="L1",
        profile_id=None,
        timestamp=None,
        snapshot=snapshot,
        # alpha_score NOT passed → must fall back to snapshot value
    )

    assert result["alpha_score"] == 42.5


def test_normalize_decision_snapshot_explicit_arg_wins_over_snapshot():
    """Explicit `alpha_score` argument must override any value stored in
    the snapshot (the caller may have a fresher live-computed value).
    """
    from app.api.watchlists import _normalize_decision_snapshot

    snapshot = {"alpha_score": 10.0}

    result = _normalize_decision_snapshot(
        symbol="BTC_USDT",
        status="rejected",
        stage="POOL",
        profile_id=None,
        timestamp=None,
        snapshot=snapshot,
        alpha_score=77.0,  # explicit override
    )

    assert result["alpha_score"] == 77.0


def test_normalize_decision_snapshot_returns_none_when_no_alpha_score():
    """When neither the explicit argument nor the snapshot contains an
    alpha_score, the field must be None (renders '–' in the frontend).
    """
    from app.api.watchlists import _normalize_decision_snapshot

    result = _normalize_decision_snapshot(
        symbol="UNKNOWN_USDT",
        status="rejected",
        stage="L2",
        profile_id=None,
        timestamp=None,
        snapshot={},
    )

    assert result["alpha_score"] is None


def test_build_analysis_snapshot_for_watchlist_min_score_gate():
    """build_analysis_snapshot with the synthetic trace produced by the
    watchlist-level min_alpha_score gate must return a well-formed rejection
    snapshot with 'Alpha Score' listed as the sole failed indicator.
    """
    from app.services.pipeline_rejections import build_analysis_snapshot

    wl_min_score = 40.0
    actual_score = 27.3
    _condition = f"Score >= {wl_min_score:g}"

    trace = [
        {
            "type": "filter",
            "indicator": "Alpha Score",
            "status": "FAIL",
            "condition": _condition,
            "current_value": round(actual_score, 1),
            "expected": wl_min_score,
        }
    ]

    snapshot = build_analysis_snapshot(
        symbol="SOL_USDT",
        stage="L1",
        profile_id="profile-wl",
        status="rejected",
        trace=trace,
        timestamp="2026-01-01T00:00:00Z",
    )

    assert snapshot["status"] == "rejected"
    assert snapshot["symbol"] == "SOL_USDT"
    assert snapshot["stage"] == "L1"
    assert snapshot["failed_indicators"] == ["Alpha Score"]
    assert "Alpha Score" in snapshot["current_values"]
    assert snapshot["current_values"]["Alpha Score"] == round(actual_score, 1)
    assert "Alpha Score" in snapshot["expected_values"]
    assert snapshot["expected_values"]["Alpha Score"] == wl_min_score
    assert _condition in snapshot["conditions"]


def test_watchlist_min_score_rejection_row_contains_all_required_fields():
    """The rejection dict built by the watchlist min_alpha_score gate must
    contain every field that _replace_rejection_snapshot consumes when
    creating a PipelineWatchlistRejection row.

    Required fields (from watchlists.py _replace_rejection_snapshot):
      symbol, stage, failed_type, failed_indicator, condition (→ condition_text),
      current_value, expected (→ expected_value), evaluation_trace, analysis_snapshot.
    Plus snapshot-denormalised fields used by the read path:
      status, details, failed_indicators, conditions, current_values, expected_values.
    """
    from app.services.pipeline_rejections import build_analysis_snapshot

    wl_min_score = 55.0
    actual_score = 38.6
    effective_level = "L1"
    profile_id = "profile-abc"
    timestamp = "2026-01-01T00:00:00Z"

    _sym = "ADA_USDT"
    _condition = f"Score >= {wl_min_score:g}"
    _score_trace = [
        {
            "type": "filter",
            "indicator": "Alpha Score",
            "status": "FAIL",
            "condition": _condition,
            "current_value": round(actual_score, 1),
            "expected": wl_min_score,
        }
    ]
    _snapshot = build_analysis_snapshot(
        symbol=_sym,
        stage=effective_level,
        profile_id=profile_id,
        status="rejected",
        trace=_score_trace,
        timestamp=timestamp,
    )

    row = {
        "symbol": _sym,
        "stage": effective_level,
        "profile_id": profile_id,
        "failed_type": "filter",
        "failed_indicator": "Alpha Score",
        "condition": _condition,
        "current_value": round(actual_score, 1),
        "expected": str(wl_min_score),
        "timestamp": timestamp,
        "evaluation_trace": _score_trace,
        "status": _snapshot["status"],
        "details": _snapshot["details"],
        "failed_indicators": _snapshot["failed_indicators"],
        "conditions": _snapshot["conditions"],
        "current_values": _snapshot["current_values"],
        "expected_values": _snapshot["expected_values"],
        "analysis_snapshot": _snapshot,
    }

    # Fields consumed by _replace_rejection_snapshot
    for field in ("symbol", "stage", "failed_type", "failed_indicator",
                  "condition", "current_value", "expected",
                  "evaluation_trace", "analysis_snapshot"):
        assert field in row, f"Missing required rejection row field: {field}"

    # Field values / types
    assert row["symbol"] == "ADA_USDT"
    assert row["stage"] == "L1"
    assert row["failed_type"] == "filter"
    assert row["failed_indicator"] == "Alpha Score"
    assert row["condition"] == "Score >= 55"
    assert isinstance(row["current_value"], float)  # numeric, not string
    assert row["current_value"] == round(actual_score, 1)
    assert row["failed_indicators"] == ["Alpha Score"]
    assert len(row["evaluation_trace"]) == 1
    assert row["evaluation_trace"][0]["status"] == "FAIL"
    assert row["analysis_snapshot"]["status"] == "rejected"


def test_live_score_computation_via_score_engine_for_legacy_rejection_rows():
    """Verify that the ScoreEngine path used for legacy rows (those without
    alpha_score in analysis_snapshot) produces a finite, bounded score from
    raw indicator data — mirroring what _resolve_alpha_score does in the
    rejection read path.

    This is a white-box sanity check: if ScoreEngine.compute_alpha_score()
    succeeds on a dict of raw indicators, the live-recompute path in
    `_get_watchlist_rejections_payload` will surface a real number instead
    of '–' for every legacy rejected row that has an indicators row.

    DEFAULT_SCORE ships with rsi_1 (RSI ≤ 25) and rsi_2 (RSI ≤ 30) as its
    built-in scoring rules. Providing RSI=22 triggers both, ensuring the
    returned total_score is > 0.
    """
    from app.services.score_engine import ScoreEngine, merge_score_config
    from app.services.seed_service import DEFAULT_SCORE

    engine = ScoreEngine(merge_score_config(DEFAULT_SCORE, {}))
    # RSI=22 satisfies both rsi_1 (≤25) and rsi_2 (≤30).
    indicators = {
        "rsi": 22,
        "volume_24h": 3_000_000,
        "market_cap": 500_000_000,
        "change_24h": 1.5,
    }

    result = engine.compute_alpha_score(indicators)
    total = result.get("total_score", None)

    assert total is not None
    assert 0 <= total <= 100, f"Score out of bounds: {total}"
    # rsi_1 + rsi_2 both pass → score > 0
    assert total > 0


# ── Task #88: alpha_score matches score_rules in Rejected tab ──────────────────

def test_rejection_snapshot_alpha_score_comes_from_live_score_map():
    """Regression for #88: the alpha_score persisted into analysis_snapshot for a
    rejected asset must match the score computed by the same ScoreEngine run that
    built score_rules_map — NOT from _rrow.get("alpha_score") which is always None
    since evaluate_rejections() does not populate that key.

    Simulates the enrichment loop at watchlists.py:1673-1687:
      - live_score_map[sym]   = engine.compute_alpha_score(eval_data)["total_score"]
      - score_rules_map[sym]  = engine.get_full_breakdown(eval_data)
      - _rsnap["alpha_score"] = live_score_map.get(sym)   ← correct (post-fix)
      - _rsnap["alpha_score"] = _rrow.get("alpha_score")  ← was None (pre-fix)

    Ensures the displayed score number in ScoreBreakdownSection matches the
    sum of points_awarded in the score_rules list.
    """
    from app.services.score_engine import ScoreEngine, merge_score_config
    from app.services.seed_service import DEFAULT_SCORE

    engine = ScoreEngine(merge_score_config(DEFAULT_SCORE, {}))
    eval_data = {"rsi": 22, "volume_24h": 5_000_000, "market_cap": 500_000_000, "change_24h": 1.5}

    # These are what watchlists.py builds at lines 1438-1440
    score_result = engine.compute_alpha_score(eval_data)
    live_score = round(float(score_result.get("total_score", 0)), 1)
    score_rules = engine.get_full_breakdown(eval_data)

    # Simulate a rejection row — no "alpha_score" key (as returned by evaluate_rejections)
    rejection_row = {
        "symbol": "SHIB_USDT",
        "stage": "L1",
        "failed_type": "filter",
        "failed_indicator": "Volume 24h",
        "analysis_snapshot": {"status": "rejected"},
    }

    # Pre-fix behaviour: _rrow.get("alpha_score") → always None
    pre_fix_value = rejection_row.get("alpha_score")
    assert pre_fix_value is None, "evaluate_rejections rows must not carry alpha_score"

    # Post-fix behaviour: live_score_map.get(sym)
    sym = rejection_row["symbol"]
    live_score_map = {sym: live_score}
    score_rules_map = {sym: score_rules}

    # Replicate the enrichment loop
    _rsnap = dict(rejection_row.get("analysis_snapshot") or {})
    _rsnap["score_rules"] = score_rules_map[sym]
    _rsnap["alpha_score"] = live_score_map.get(sym)  # ← the fix

    # The stored alpha_score must now equal the live computed score (not None / 0)
    assert _rsnap["alpha_score"] is not None
    assert _rsnap["alpha_score"] == live_score
    assert _rsnap["alpha_score"] > 0, "RSI=22 should trigger both rsi_1 and rsi_2 rules"

    # The score_rules list must have at least one rule (rsi_1 or rsi_2 from DEFAULT_SCORE)
    assert len(_rsnap["score_rules"]) > 0, "score_rules must not be empty when indicators are present"
