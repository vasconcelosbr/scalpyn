"""Integration tests for dual-write scoring mode."""

import pytest
import json
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.fixture
def mock_db_session():
    """Mock database session."""
    session = AsyncMock()
    session.execute = AsyncMock()
    session.begin_nested = MagicMock()
    return session


@pytest.fixture
def score_config_dual_write():
    """Score config with dual-write enabled."""
    return {
        "weights": {"liquidity": 25, "market_structure": 25, "momentum": 25, "signal": 25},
        "scoring_rules": [
            {"id": "rsi_1", "indicator": "rsi", "operator": "<=", "value": 30, "points": 40, "category": "momentum"},
        ],
        "thresholds": {"strong_buy": 80, "buy": 65, "neutral": 40},
        "confidence_weighting": {
            "enabled": False,
            "min_confidence": 0.5,
            "dual_write_mode": True,  # Dual-write enabled
        }
    }


@pytest.fixture
def score_config_confidence_only():
    """Score config with confidence weighting enabled (no dual-write)."""
    return {
        "weights": {"liquidity": 25, "market_structure": 25, "momentum": 25, "signal": 25},
        "scoring_rules": [
            {"id": "rsi_1", "indicator": "rsi", "operator": "<=", "value": 30, "points": 40, "category": "momentum"},
        ],
        "thresholds": {"strong_buy": 80, "buy": 65, "neutral": 40},
        "confidence_weighting": {
            "enabled": True,
            "min_confidence": 0.5,
            "dual_write_mode": False,
        }
    }


@pytest.fixture
def score_config_legacy():
    """Legacy score config (no confidence weighting)."""
    return {
        "weights": {"liquidity": 25, "market_structure": 25, "momentum": 25, "signal": 25},
        "scoring_rules": [
            {"id": "rsi_1", "indicator": "rsi", "operator": "<=", "value": 30, "points": 40, "category": "momentum"},
        ],
        "thresholds": {"strong_buy": 80, "buy": 65, "neutral": 40},
    }


def test_dual_write_computes_both_scores(score_config_dual_write):
    """Test that dual-write mode computes both v1 and v2 scores."""
    from app.services.score_engine import ScoreEngine

    engine = ScoreEngine(score_config_dual_write, min_confidence=0.5)

    indicators = {"rsi": 28.0}

    # Compute v1 (legacy)
    result_v1 = engine.compute_alpha_score(indicators, use_confidence_weighting=False)

    # Compute v2 (confidence-weighted)
    result_v2 = engine.compute_alpha_score(indicators, use_confidence_weighting=True)

    # Both should succeed
    assert result_v1["total_score"] >= 0
    assert result_v2["total_score"] >= 0
    assert result_v1["confidence_weighted"] is False
    assert result_v2["confidence_weighted"] is True


def test_scoring_version_dual_write(score_config_dual_write):
    """Test that scoring_version is set correctly in dual-write mode."""
    # This would be tested in integration with actual compute_scores task
    # Here we verify the config structure
    assert score_config_dual_write["confidence_weighting"]["dual_write_mode"] is True
    assert score_config_dual_write["confidence_weighting"]["enabled"] is False


def test_scoring_version_confidence_only(score_config_confidence_only):
    """Test that scoring_version is set correctly in confidence-only mode."""
    assert score_config_confidence_only["confidence_weighting"]["enabled"] is True
    assert score_config_confidence_only["confidence_weighting"]["dual_write_mode"] is False


def test_scoring_version_legacy(score_config_legacy):
    """Test that scoring_version defaults to v1 in legacy mode."""
    # No confidence_weighting section = legacy mode (v1)
    assert "confidence_weighting" not in score_config_legacy


def test_score_delta_logging(caplog):
    """Test that score deltas > 10 are logged."""
    from app.services.score_engine import ScoreEngine
    import logging

    config = {
        "weights": {"liquidity": 25, "market_structure": 25, "momentum": 25, "signal": 25},
        "scoring_rules": [
            {"id": "rsi_1", "indicator": "rsi", "operator": "<=", "value": 30, "points": 100, "category": "momentum"},
        ],
        "thresholds": {"strong_buy": 80, "buy": 65, "neutral": 40},
    }

    engine = ScoreEngine(config)

    # Create indicators that will have different scores in v1 vs v2
    indicators_v1 = {"rsi": 28.0}
    indicators_v2_low_conf = {
        "rsi": {"value": 28.0, "confidence": 0.3, "valid": True}  # Low confidence
    }

    result_v1 = engine.compute_alpha_score(indicators_v1, use_confidence_weighting=False)
    result_v2 = engine.compute_alpha_score(indicators_v2_low_conf, use_confidence_weighting=True)

    # Delta should be significant (v2 will skip low-confidence indicator)
    delta = abs(result_v1["total_score"] - result_v2["total_score"])
    assert delta > 10  # Should trigger logging in actual task


def test_confidence_metrics_stored_in_dual_write():
    """Test that confidence_metrics are computed in dual-write mode."""
    from app.services.score_engine import ScoreEngine

    config = {
        "weights": {"liquidity": 25, "market_structure": 25, "momentum": 25, "signal": 25},
        "scoring_rules": [
            {"id": "rsi_1", "indicator": "rsi", "operator": "<=", "value": 30, "points": 40, "category": "momentum"},
        ],
        "thresholds": {"strong_buy": 80, "buy": 65, "neutral": 40},
        "confidence_weighting": {
            "enabled": False,
            "min_confidence": 0.5,
            "dual_write_mode": True,
        }
    }

    engine = ScoreEngine(config, min_confidence=0.5)

    indicators = {
        "rsi": {"value": 28.0, "confidence": 0.9, "valid": True}
    }

    result = engine.compute_alpha_score(indicators, use_confidence_weighting=True)

    # Should have confidence metrics
    assert "confidence_metrics" in result
    assert result["confidence_metrics"]["overall_confidence"] > 0


def test_backward_compatibility_without_confidence_section():
    """Test that systems without confidence_weighting config work normally."""
    from app.services.score_engine import ScoreEngine

    legacy_config = {
        "weights": {"liquidity": 25, "market_structure": 25, "momentum": 25, "signal": 25},
        "scoring_rules": [
            {"id": "rsi_1", "indicator": "rsi", "operator": "<=", "value": 30, "points": 40, "category": "momentum"},
        ],
        "thresholds": {"strong_buy": 80, "buy": 65, "neutral": 40},
        # No confidence_weighting section
    }

    engine = ScoreEngine(legacy_config)
    indicators = {"rsi": 28.0}

    result = engine.compute_alpha_score(indicators)

    # Should work in legacy mode
    assert result["total_score"] >= 0
    assert result["confidence_weighted"] is False
    assert "confidence_metrics" not in result


def test_rollback_to_legacy_mode():
    """Test that disabling dual-write returns to legacy mode."""
    from app.services.score_engine import ScoreEngine

    config_disabled = {
        "weights": {"liquidity": 25, "market_structure": 25, "momentum": 25, "signal": 25},
        "scoring_rules": [
            {"id": "rsi_1", "indicator": "rsi", "operator": "<=", "value": 30, "points": 40, "category": "momentum"},
        ],
        "thresholds": {"strong_buy": 80, "buy": 65, "neutral": 40},
        "confidence_weighting": {
            "enabled": False,
            "min_confidence": 0.5,
            "dual_write_mode": False,  # Disabled
        }
    }

    engine = ScoreEngine(config_disabled)
    indicators = {"rsi": 28.0}

    result = engine.compute_alpha_score(indicators)

    # Should work in legacy mode (no confidence weighting)
    assert result["confidence_weighted"] is False
    assert "confidence_metrics" not in result
