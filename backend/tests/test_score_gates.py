"""Regression tests for the robust scoring engine (Task #203).

Covers:
  - Partial data scoring (missing critical indicators)
  - Low-confidence scoring (below threshold but still computed)
  - Between / EMA-trend / DI-comparison operator handling
  - Breakdown consistency with partial data
  - Full-data scoring unchanged
  - Zero-indicator edge case
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.services.robust_indicators.envelope import (
    DataSource,
    IndicatorEnvelope,
    IndicatorStatus,
)
from app.services.robust_indicators.score import (
    ScoreResult,
    calculate_score_with_confidence,
)
from app.services.robust_indicators.asset_score import compute_asset_score


def _env(name: str, value, *, source=DataSource.GATE_CANDLES,
         status=IndicatorStatus.VALID, confidence=0.85):
    now = datetime.now(timezone.utc)
    return IndicatorEnvelope(
        name=name, value=value, status=status, source=source,
        timestamp=now, confidence=confidence, base_confidence=confidence,
        staleness_seconds=0.0,
    )


def _no_data(name: str):
    now = datetime.now(timezone.utc)
    return IndicatorEnvelope(
        name=name, value=None, status=IndicatorStatus.NO_DATA,
        source=DataSource.UNKNOWN, timestamp=now,
        confidence=0.0, base_confidence=0.0, staleness_seconds=0.0,
    )


RULES_LIKE_USER = [
    {"id": "r_rsi", "indicator": "rsi", "operator": "between",
     "min": 48, "max": 62, "points": 15, "category": "momentum"},
    {"id": "r_macd", "indicator": "macd_histogram", "operator": ">",
     "value": 0, "points": 15, "category": "momentum"},
    {"id": "r_ema", "indicator": "ema_trend", "operator": "ema50>ema200",
     "value": 10, "points": 10, "category": "market_structure"},
    {"id": "r_adx", "indicator": "adx", "operator": ">",
     "value": 15, "points": 10, "category": "market_structure"},
    {"id": "r_bb", "indicator": "bb_width", "operator": "between",
     "min": 0.01, "max": 0.05, "points": 10, "category": "market_structure"},
    {"id": "r_vol24h", "indicator": "volume_24h", "operator": ">",
     "value": 10000, "points": 10, "category": "liquidity"},
    {"id": "r_diplus", "indicator": "di_plus", "operator": "between",
     "min": 30, "max": 60, "points": 10, "category": "market_structure"},
    {"id": "r_depth", "indicator": "orderbook_depth_usdt", "operator": ">=",
     "value": 5000, "points": 10, "category": "liquidity"},
    {"id": "r_vd1", "indicator": "volume_delta", "operator": "between",
     "min": 50, "max": 200, "points": 10, "category": "signal"},
    {"id": "r_vd2", "indicator": "volume_delta", "operator": "between",
     "min": 10, "max": 50, "points": 5, "category": "signal"},
    {"id": "r_vd3", "indicator": "volume_delta", "operator": ">=",
     "value": 200, "points": 15, "category": "signal"},
]

TOTAL_POSITIVE_POINTS = sum(r["points"] for r in RULES_LIKE_USER)


class TestCriticalGateRemoved:
    def test_partial_data_ema_only(self):
        envelopes = {
            "ema50_gt_ema200": _env("ema50_gt_ema200", True, source=DataSource.DERIVED, confidence=0.80),
        }
        result = calculate_score_with_confidence(envelopes, RULES_LIKE_USER)

        assert not result.rejected
        assert result.score > 0.0, f"Expected non-zero score, got {result.score}"
        expected = (10 * 0.80) / TOTAL_POSITIVE_POINTS * 100
        assert abs(result.score - round(expected, 2)) < 0.1
        assert len(result.matched_rules) == 1
        assert result.matched_rules[0]["rule_id"] == "r_ema"

    def test_partial_data_rsi_only(self):
        envelopes = {
            "rsi": _env("rsi", 55.0),
        }
        result = calculate_score_with_confidence(envelopes, RULES_LIKE_USER)

        assert not result.rejected
        assert result.score > 0.0
        expected = (15 * 0.85) / TOTAL_POSITIVE_POINTS * 100
        assert abs(result.score - round(expected, 2)) < 0.1

    def test_missing_all_critical_still_scores(self):
        envelopes = {
            "volume_24h": _env("volume_24h", 50000.0, source=DataSource.GATE_TICKER),
            "orderbook_depth_usdt": _env("orderbook_depth_usdt", 10000.0, source=DataSource.GATE_ORDERBOOK),
        }
        result = calculate_score_with_confidence(envelopes, RULES_LIKE_USER)

        assert not result.rejected
        assert result.score > 0.0
        assert len(result.matched_rules) == 2

    def test_zero_envelopes_yields_zero(self):
        result = calculate_score_with_confidence({}, RULES_LIKE_USER)
        assert result.score == 0.0
        assert len(result.matched_rules) == 0

    def test_no_rules_yields_zero(self):
        envelopes = {"rsi": _env("rsi", 55.0)}
        result = calculate_score_with_confidence(envelopes, [])
        assert result.score == 0.0


class TestConfidenceGateSoftened:
    def test_low_confidence_still_computes_score(self):
        envelopes = {
            "rsi": _env("rsi", 55.0, confidence=0.30),
        }
        result = calculate_score_with_confidence(
            envelopes, RULES_LIKE_USER, min_global_confidence=0.60,
        )
        assert not result.rejected
        assert result.score > 0.0
        assert not result.can_trade

    def test_high_score_confidence_but_low_global_confidence_blocks_trade(self):
        envelopes = {
            "rsi": _env("rsi", 55.0, confidence=0.95),
            "adx": _env("adx", 25.0, confidence=0.95),
            "macd_histogram": _env("macd_histogram", 0.5, confidence=0.95),
            "ema50": _env("ema50", 100.0, confidence=0.05),
            "ema50_gt_ema200": _env("ema50_gt_ema200", True,
                                    source=DataSource.DERIVED, confidence=0.95),
            "bb_width": _env("bb_width", 0.03, confidence=0.95),
            "volume_24h": _env("volume_24h", 50000.0,
                               source=DataSource.GATE_TICKER, confidence=0.05),
            "di_plus": _env("di_plus", 40.0, confidence=0.95),
            "orderbook_depth_usdt": _env("orderbook_depth_usdt", 10000.0,
                                         source=DataSource.GATE_ORDERBOOK, confidence=0.05),
            "volume_delta": _env("volume_delta", 100.0,
                                 source=DataSource.GATE_TRADES, confidence=0.05),
        }
        result = calculate_score_with_confidence(
            envelopes, RULES_LIKE_USER, can_trade_threshold=30.0,
            min_global_confidence=0.60,
        )
        assert not result.rejected
        assert result.score > 30.0
        assert result.global_confidence < 0.60
        assert not result.can_trade

    def test_high_confidence_can_trade(self):
        envelopes = {
            "rsi": _env("rsi", 55.0, confidence=0.95),
            "adx": _env("adx", 25.0, confidence=0.95),
            "macd_histogram": _env("macd_histogram", 0.5, confidence=0.95),
            "ema50": _env("ema50", 100.0, confidence=0.95),
            "ema50_gt_ema200": _env("ema50_gt_ema200", True,
                                    source=DataSource.DERIVED, confidence=0.95),
            "bb_width": _env("bb_width", 0.03, confidence=0.95),
            "volume_24h": _env("volume_24h", 50000.0,
                               source=DataSource.GATE_TICKER, confidence=0.95),
            "di_plus": _env("di_plus", 40.0, confidence=0.95),
            "orderbook_depth_usdt": _env("orderbook_depth_usdt", 10000.0,
                                         source=DataSource.GATE_ORDERBOOK, confidence=0.95),
            "volume_delta": _env("volume_delta", 100.0,
                                 source=DataSource.GATE_TRADES, confidence=0.95),
        }
        result = calculate_score_with_confidence(
            envelopes, RULES_LIKE_USER, can_trade_threshold=30.0,
        )
        assert not result.rejected
        assert result.score > 30.0
        assert result.can_trade


class TestBetweenOperator:
    def test_between_match(self):
        envelopes = {"rsi": _env("rsi", 55.0)}
        rules = [{"id": "r1", "indicator": "rsi", "operator": "between",
                  "min": 48, "max": 62, "points": 10, "category": "momentum"}]
        result = calculate_score_with_confidence(envelopes, rules)
        assert result.score == round((10 * 0.85 / 10) * 100, 2)
        assert len(result.matched_rules) == 1

    def test_between_no_match_below(self):
        envelopes = {"rsi": _env("rsi", 30.0)}
        rules = [{"id": "r1", "indicator": "rsi", "operator": "between",
                  "min": 48, "max": 62, "points": 10, "category": "momentum"}]
        result = calculate_score_with_confidence(envelopes, rules)
        assert result.score == 0.0

    def test_between_no_match_above(self):
        envelopes = {"rsi": _env("rsi", 80.0)}
        rules = [{"id": "r1", "indicator": "rsi", "operator": "between",
                  "min": 48, "max": 62, "points": 10, "category": "momentum"}]
        result = calculate_score_with_confidence(envelopes, rules)
        assert result.score == 0.0


class TestEmaTrendOperators:
    def test_ema50_gt_ema200_match(self):
        envelopes = {
            "ema50_gt_ema200": _env("ema50_gt_ema200", True,
                                    source=DataSource.DERIVED, confidence=0.80),
        }
        rules = [{"id": "r1", "indicator": "ema_trend",
                  "operator": "ema50>ema200", "value": 10,
                  "points": 10, "category": "market_structure"}]
        result = calculate_score_with_confidence(envelopes, rules)
        assert result.score > 0.0
        assert len(result.matched_rules) == 1
        assert result.matched_rules[0]["indicator"] == "ema_trend"

    def test_ema50_gt_ema200_no_match(self):
        envelopes = {
            "ema50_gt_ema200": _env("ema50_gt_ema200", False,
                                    source=DataSource.DERIVED, confidence=0.80),
        }
        rules = [{"id": "r1", "indicator": "ema_trend",
                  "operator": "ema50>ema200", "value": 10,
                  "points": 10, "category": "market_structure"}]
        result = calculate_score_with_confidence(envelopes, rules)
        assert result.score == 0.0

    def test_ema9_gt_ema50_match(self):
        envelopes = {
            "ema9_gt_ema50": _env("ema9_gt_ema50", True,
                                  source=DataSource.DERIVED, confidence=0.80),
        }
        rules = [{"id": "r1", "indicator": "ema_trend",
                  "operator": "ema9>ema50", "points": 10,
                  "category": "market_structure"}]
        result = calculate_score_with_confidence(envelopes, rules)
        assert result.score > 0.0

    def test_ema9_lt_ema50_match(self):
        envelopes = {
            "ema9_gt_ema50": _env("ema9_gt_ema50", False,
                                  source=DataSource.DERIVED, confidence=0.80),
        }
        rules = [{"id": "r1", "indicator": "ema_trend",
                  "operator": "ema9<ema50", "points": 10,
                  "category": "market_structure"}]
        result = calculate_score_with_confidence(envelopes, rules)
        assert result.score > 0.0

    def test_ema_full_alignment_match(self):
        envelopes = {
            "ema_full_alignment": _env("ema_full_alignment", True,
                                       source=DataSource.DERIVED, confidence=0.80),
        }
        rules = [{"id": "r1", "indicator": "ema_trend",
                  "operator": "ema9>ema50>ema200", "points": 30,
                  "category": "market_structure"}]
        result = calculate_score_with_confidence(envelopes, rules)
        assert result.score > 0.0


class TestDiComparisonOperators:
    def test_di_plus_gt_di_minus(self):
        envelopes = {
            "di_plus": _env("di_plus", 40.0),
            "di_minus": _env("di_minus", 20.0),
        }
        rules = [{"id": "r1", "indicator": "di_plus",
                  "operator": "di+>di-", "points": 10,
                  "category": "market_structure"}]
        result = calculate_score_with_confidence(envelopes, rules)
        assert result.score > 0.0

    def test_di_minus_gt_di_plus(self):
        envelopes = {
            "di_plus": _env("di_plus", 15.0),
            "di_minus": _env("di_minus", 30.0),
        }
        rules = [{"id": "r1", "indicator": "di_plus",
                  "operator": "di->di+", "points": 10,
                  "category": "market_structure"}]
        result = calculate_score_with_confidence(envelopes, rules)
        assert result.score > 0.0

    def test_di_comparison_missing_one(self):
        envelopes = {
            "di_plus": _env("di_plus", 40.0),
        }
        rules = [{"id": "r1", "indicator": "di_plus",
                  "operator": "di+>di-", "points": 10,
                  "category": "market_structure"}]
        result = calculate_score_with_confidence(envelopes, rules)
        assert result.score == 0.0


class TestComputeAssetScorePartialData:
    def test_partial_data_returns_score(self):
        indicators = {"ema50_gt_ema200": True}
        rules = [{"id": "r1", "indicator": "ema_trend",
                  "operator": "ema50>ema200", "value": 10,
                  "points": 10, "category": "market_structure"}]
        payload = compute_asset_score("ZEC_USDT", indicators, rules)
        assert payload is not None
        assert payload["score"] > 0.0

    def test_empty_indicators_returns_none(self):
        rules = [{"id": "r1", "indicator": "rsi", "operator": ">",
                  "value": 50, "points": 10, "category": "momentum"}]
        payload = compute_asset_score("ZEC_USDT", {}, rules)
        assert payload is None

    def test_full_data_scores_correctly(self):
        indicators = {
            "rsi": 55.0,
            "adx": 25.0,
            "macd_histogram": 0.5,
            "ema50": 100.0,
            "ema50_gt_ema200": True,
            "bb_width": 0.03,
            "volume_24h": 50000.0,
            "di_plus": 40.0,
            "orderbook_depth_usdt": 10000.0,
            "volume_delta": 100.0,
        }
        payload = compute_asset_score("BTC_USDT", indicators, RULES_LIKE_USER)
        assert payload is not None
        assert payload["score"] > 0.0
        assert len(payload["matched_rules"]) > 0


class TestBreakdownConsistency:
    def test_matched_rules_have_weighted_points(self):
        envelopes = {
            "rsi": _env("rsi", 55.0, confidence=0.85),
            "ema50_gt_ema200": _env("ema50_gt_ema200", True,
                                    source=DataSource.DERIVED, confidence=0.80),
        }
        result = calculate_score_with_confidence(envelopes, RULES_LIKE_USER)

        assert len(result.matched_rules) == 2
        for mr in result.matched_rules:
            assert "weighted_points" in mr
            assert "confidence" in mr
            assert mr["weighted_points"] > 0.0

    def test_score_matches_weighted_sum(self):
        envelopes = {
            "rsi": _env("rsi", 55.0, confidence=0.90),
            "adx": _env("adx", 25.0, confidence=0.90),
        }
        rules = [
            {"id": "r1", "indicator": "rsi", "operator": "between",
             "min": 48, "max": 62, "points": 15, "category": "momentum"},
            {"id": "r2", "indicator": "adx", "operator": ">",
             "value": 15, "points": 10, "category": "market_structure"},
        ]
        result = calculate_score_with_confidence(envelopes, rules)

        weighted_sum = sum(mr["weighted_points"] for mr in result.matched_rules)
        denom = sum(r["points"] for r in rules)
        expected_score = round((weighted_sum / denom) * 100.0, 2)
        assert result.score == expected_score


class TestFullDataUnchanged:
    def test_all_indicators_present_unchanged(self):
        envelopes = {
            "rsi": _env("rsi", 55.0, confidence=0.85),
            "adx": _env("adx", 25.0, confidence=0.85),
            "macd": _env("macd", 0.5, confidence=0.85),
            "macd_histogram": _env("macd_histogram", 0.5, confidence=0.85),
            "ema50": _env("ema50", 100.0, confidence=0.85),
            "ema50_gt_ema200": _env("ema50_gt_ema200", True,
                                    source=DataSource.DERIVED, confidence=0.80),
            "bb_width": _env("bb_width", 0.03, confidence=0.85),
            "volume_24h": _env("volume_24h", 50000.0,
                               source=DataSource.GATE_TICKER, confidence=0.85),
            "di_plus": _env("di_plus", 40.0, confidence=0.85),
            "orderbook_depth_usdt": _env("orderbook_depth_usdt", 10000.0,
                                         source=DataSource.GATE_ORDERBOOK, confidence=0.90),
            "volume_delta": _env("volume_delta", 100.0,
                                 source=DataSource.GATE_TRADES, confidence=1.0),
        }
        result = calculate_score_with_confidence(envelopes, RULES_LIKE_USER)

        assert not result.rejected
        assert result.rejection_reason is None
        assert result.score > 0.0
        assert len(result.matched_rules) >= 5
