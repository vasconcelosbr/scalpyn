"""Unit tests for confidence-weighted scoring in ScoreEngine."""

import pytest
from app.services.score_engine import ScoreEngine


@pytest.fixture
def basic_score_config():
    """Basic score configuration for testing."""
    return {
        "weights": {"liquidity": 25, "market_structure": 25, "momentum": 25, "signal": 25},
        "scoring_rules": [
            {"id": "rsi_1", "indicator": "rsi", "operator": "<=", "value": 30, "points": 40, "category": "momentum"},
            {"id": "adx_1", "indicator": "adx", "operator": ">=", "value": 25, "points": 30, "category": "market_structure"},
            {"id": "vol_1", "indicator": "volume_24h_usdt", "operator": ">=", "value": 1000000, "points": 20, "category": "liquidity"},
        ],
        "thresholds": {"strong_buy": 80, "buy": 65, "neutral": 40},
    }


@pytest.fixture
def indicator_envelopes_high_conf():
    """Indicator envelopes with high confidence."""
    return {
        "rsi": {
            "value": 28.0,
            "confidence": 0.95,
            "valid": True,
            "source": "gate",
            "status": "PASS",
        },
        "adx": {
            "value": 30.0,
            "confidence": 0.90,
            "valid": True,
            "source": "gate",
            "status": "PASS",
        },
        "volume_24h_usdt": {
            "value": 2000000.0,
            "confidence": 0.85,
            "valid": True,
            "source": "gate",
            "status": "PASS",
        },
    }


@pytest.fixture
def indicator_envelopes_low_conf():
    """Indicator envelopes with low confidence."""
    return {
        "rsi": {
            "value": 28.0,
            "confidence": 0.35,  # Below 0.5 threshold
            "valid": True,
            "source": "candle_approx",
            "status": "PASS",
        },
        "adx": {
            "value": 30.0,
            "confidence": 0.40,  # Below 0.5 threshold
            "valid": True,
            "source": "candle_approx",
            "status": "PASS",
        },
        "volume_24h_usdt": {
            "value": 2000000.0,
            "confidence": 0.85,
            "valid": True,
            "source": "gate",
            "status": "PASS",
        },
    }


@pytest.fixture
def raw_indicators():
    """Raw indicators (legacy format)."""
    return {
        "rsi": 28.0,
        "adx": 30.0,
        "volume_24h_usdt": 2000000.0,
    }


def test_confidence_weighted_mode_detection(basic_score_config, indicator_envelopes_high_conf, raw_indicators):
    """Test auto-detection of confidence-weighted mode."""
    engine = ScoreEngine(basic_score_config)

    # Should detect envelope mode
    assert engine._is_confidence_weighted_mode(indicator_envelopes_high_conf) is True

    # Should detect raw mode
    assert engine._is_confidence_weighted_mode(raw_indicators) is False


def test_extract_value_and_confidence(basic_score_config, indicator_envelopes_high_conf, raw_indicators):
    """Test extraction of value and confidence from indicators."""
    engine = ScoreEngine(basic_score_config)

    # From envelope
    value, conf, valid = engine._extract_value_and_confidence(indicator_envelopes_high_conf, "rsi")
    assert value == 28.0
    assert conf == 0.95
    assert valid is True

    # From raw
    value, conf, valid = engine._extract_value_and_confidence(raw_indicators, "rsi")
    assert value == 28.0
    assert conf == 1.0
    assert valid is True

    # Missing indicator
    value, conf, valid = engine._extract_value_and_confidence({}, "rsi")
    assert value is None
    assert conf == 0.0
    assert valid is False


def test_confidence_weighted_scoring_high_confidence(basic_score_config, indicator_envelopes_high_conf):
    """Test confidence-weighted scoring with high confidence indicators."""
    engine = ScoreEngine(basic_score_config, min_confidence=0.5)

    result = engine.compute_alpha_score(indicator_envelopes_high_conf, use_confidence_weighting=True)

    # Should pass all rules since confidence is high
    assert result["total_score"] > 0
    assert result["confidence_weighted"] is True
    assert "confidence_metrics" in result
    assert result["confidence_metrics"]["overall_confidence"] > 0.85


def test_confidence_weighted_scoring_low_confidence(basic_score_config, indicator_envelopes_low_conf):
    """Test confidence-weighted scoring with low confidence indicators."""
    engine = ScoreEngine(basic_score_config, min_confidence=0.5)

    result = engine.compute_alpha_score(indicator_envelopes_low_conf, use_confidence_weighting=True)

    # Should skip low-confidence indicators (rsi, adx)
    assert result["confidence_weighted"] is True
    assert "confidence_metrics" in result

    # Only volume_24h_usdt should contribute (high confidence)
    # Score should be lower than high-confidence case
    assert result["confidence_metrics"]["low_confidence_rules"] >= 2


def test_confidence_multiplier_application(basic_score_config):
    """Test that confidence multiplier is correctly applied to points."""
    engine = ScoreEngine(basic_score_config, min_confidence=0.5)

    # High confidence envelope (0.9) should give 90% of points
    high_conf_env = {
        "rsi": {"value": 28.0, "confidence": 0.9, "valid": True},
        "adx": {"value": 30.0, "confidence": 0.9, "valid": True},
        "volume_24h_usdt": {"value": 2000000.0, "confidence": 0.9, "valid": True},
    }

    # Medium confidence envelope (0.6) should give 60% of points
    med_conf_env = {
        "rsi": {"value": 28.0, "confidence": 0.6, "valid": True},
        "adx": {"value": 30.0, "confidence": 0.6, "valid": True},
        "volume_24h_usdt": {"value": 2000000.0, "confidence": 0.6, "valid": True},
    }

    high_result = engine.compute_alpha_score(high_conf_env, use_confidence_weighting=True)
    med_result = engine.compute_alpha_score(med_conf_env, use_confidence_weighting=True)

    # High confidence should have higher score
    assert high_result["total_score"] > med_result["total_score"]


def test_backward_compatibility_raw_indicators(basic_score_config, raw_indicators):
    """Test that raw indicators still work (backward compatibility)."""
    engine = ScoreEngine(basic_score_config)

    # Should work with raw indicators (legacy path)
    result = engine.compute_alpha_score(raw_indicators, use_confidence_weighting=False)

    assert result["total_score"] > 0
    assert result["confidence_weighted"] is False
    assert "confidence_metrics" not in result


def test_mixed_confidence_rules(basic_score_config):
    """Test scoring with mixed confidence indicators."""
    engine = ScoreEngine(basic_score_config, min_confidence=0.5)

    mixed_env = {
        "rsi": {"value": 28.0, "confidence": 0.95, "valid": True},  # High
        "adx": {"value": 30.0, "confidence": 0.4, "valid": True},   # Low (skipped)
        "volume_24h_usdt": {"value": 2000000.0, "confidence": 0.7, "valid": True},  # Medium
    }

    result = engine.compute_alpha_score(mixed_env, use_confidence_weighting=True)

    # Should use rsi and volume_24h_usdt, skip adx
    assert result["confidence_weighted"] is True
    assert result["confidence_metrics"]["low_confidence_rules"] == 1  # adx skipped


def test_invalid_indicator_skipped(basic_score_config):
    """Test that invalid indicators are skipped."""
    engine = ScoreEngine(basic_score_config, min_confidence=0.5)

    invalid_env = {
        "rsi": {"value": 28.0, "confidence": 0.95, "valid": False},  # Invalid
        "adx": {"value": 30.0, "confidence": 0.90, "valid": True},
        "volume_24h_usdt": {"value": 2000000.0, "confidence": 0.85, "valid": True},
    }

    result = engine.compute_alpha_score(invalid_env, use_confidence_weighting=True)

    # Should skip invalid rsi
    assert result["confidence_metrics"]["low_confidence_rules"] >= 1


def test_confidence_metrics_structure(basic_score_config, indicator_envelopes_high_conf):
    """Test structure of confidence metrics in result."""
    engine = ScoreEngine(basic_score_config)

    result = engine.compute_alpha_score(indicator_envelopes_high_conf, use_confidence_weighting=True)

    assert "confidence_metrics" in result
    metrics = result["confidence_metrics"]

    assert "overall_confidence" in metrics
    assert "category_confidences" in metrics
    assert "low_confidence_rules" in metrics

    assert isinstance(metrics["overall_confidence"], (int, float))
    assert isinstance(metrics["category_confidences"], dict)
    assert isinstance(metrics["low_confidence_rules"], int)


def test_auto_detect_confidence_mode(basic_score_config, indicator_envelopes_high_conf):
    """Test that confidence mode is auto-detected when not explicitly set."""
    engine = ScoreEngine(basic_score_config)

    # Don't specify use_confidence_weighting - should auto-detect
    result = engine.compute_alpha_score(indicator_envelopes_high_conf)

    # Should automatically use confidence weighting
    assert result["confidence_weighted"] is True
    assert "confidence_metrics" in result


def test_penalty_rules_with_confidence(basic_score_config):
    """Test that penalty rules also get confidence multiplier."""
    config_with_penalty = {
        **basic_score_config,
        "scoring_rules": [
            *basic_score_config["scoring_rules"],
            {"id": "rsi_penalty", "indicator": "rsi", "operator": ">=", "value": 70, "points": -20, "category": "momentum"},
        ]
    }

    engine = ScoreEngine(config_with_penalty, min_confidence=0.5)

    # High RSI triggers penalty
    high_rsi_env = {
        "rsi": {"value": 75.0, "confidence": 0.9, "valid": True},
        "adx": {"value": 30.0, "confidence": 0.9, "valid": True},
        "volume_24h_usdt": {"value": 2000000.0, "confidence": 0.9, "valid": True},
    }

    result = engine.compute_alpha_score(high_rsi_env, use_confidence_weighting=True)

    # Penalty should be applied with confidence multiplier
    # (effective penalty = -20 * 0.9 = -18)
    assert result["confidence_weighted"] is True
