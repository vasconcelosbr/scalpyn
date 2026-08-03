from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.api.config import update_config
from app.api.social_intelligence import _enforce_ingest_token
from app.config import settings
from app.schemas.social_intelligence import SocialAssetInput, SocialRunInput, SocialScoreConfig
from app.services.shadow_trade_service import _build_features_snapshot
from app.services.social_intelligence_service import (
    apply_social_score_to_asset,
    blend_score,
    confidence_adjusted_sentiment,
    normalize_social_symbol,
    serialize_observation,
)


def _context(**overrides):
    return {
        "enabled": True,
        "eligible": True,
        "sentiment_score": 75.0,
        "attention_score": 90.0,
        "confidence": 1.0,
        "weight": 0.20,
        "fallback_reason": None,
        **overrides,
    }


def test_confidence_adjustment_is_neutral_at_zero_confidence():
    assert confidence_adjusted_sentiment(100.0, 0.0) == 50.0
    assert confidence_adjusted_sentiment(75.0, 1.0) == 75.0


@pytest.mark.parametrize("raw", ["NEAR", "near", "NEARUSDT", "NEAR/USDT", "NEAR-USDT"])
def test_social_symbol_normalization_maps_base_to_canonical_pair(raw):
    assert normalize_social_symbol(raw) == "NEAR_USDT"


def test_ingest_route_is_unavailable_without_secret(monkeypatch):
    monkeypatch.setattr(settings, "SOCIAL_INTELLIGENCE_INGEST_TOKEN", "")
    with pytest.raises(HTTPException) as exc_info:
        _enforce_ingest_token("Bearer anything")
    assert exc_info.value.status_code == 404


def test_ingest_token_requires_exact_bearer_value(monkeypatch):
    monkeypatch.setattr(settings, "SOCIAL_INTELLIGENCE_INGEST_TOKEN", "secret-value")
    with pytest.raises(HTTPException) as exc_info:
        _enforce_ingest_token("Bearer wrong")
    assert exc_info.value.status_code == 401
    _enforce_ingest_token("Bearer secret-value")


@pytest.mark.asyncio
async def test_activation_is_blocked_without_a_fresh_observation():
    class EmptyResult:
        @staticmethod
        def scalar_one_or_none():
            return None

    class EmptyDatabase:
        @staticmethod
        async def execute(_statement):
            return EmptyResult()

    with pytest.raises(HTTPException) as exc_info:
        await update_config(
            config_type="social_score",
            payload={"enabled": True},
            db=EmptyDatabase(),
            user_id=uuid4(),
        )

    assert exc_info.value.status_code == 409


def test_spot_social_modifier_uses_approved_formula():
    asset = {"_score": 80.0, "_technical_score": 80.0, "_social_score": _context()}

    result = apply_social_score_to_asset(asset, is_futures=False, technical_threshold=65.0)

    assert result["applied"] is True
    assert result["sentiment_adjusted"] == 75.0
    assert asset["_score"] == pytest.approx(79.0)
    assert result["technical_score"] == 80.0
    assert result["final_score"] == pytest.approx(79.0)


def test_futures_short_uses_sentiment_complement_without_flipping_direction():
    asset = {
        "_score": 80.0,
        "_technical_score": 80.0,
        "score_long": 72.0,
        "score_short": 80.0,
        "_technical_score_long": 72.0,
        "_technical_score_short": 80.0,
        "futures_direction": "SHORT",
        "_social_score": _context(),
    }

    result = apply_social_score_to_asset(asset, is_futures=True, technical_threshold=65.0)

    assert asset["futures_direction"] == "SHORT"
    assert asset["score_long"] == pytest.approx(blend_score(72.0, 75.0, 0.2))
    assert asset["score_short"] == pytest.approx(blend_score(80.0, 25.0, 0.2))
    assert result["final_score"] == pytest.approx(asset["score_short"])


@pytest.mark.parametrize("context_reason", ["no_observation", "stale"])
def test_missing_or_stale_social_data_preserves_technical_score(context_reason):
    asset = {
        "_score": 68.0,
        "_technical_score": 68.0,
        "_social_score": _context(eligible=False, fallback_reason=context_reason),
    }

    result = apply_social_score_to_asset(asset, is_futures=False, technical_threshold=65.0)

    assert result["applied"] is False
    assert result["fallback_reason"] == context_reason
    assert asset["_score"] == 68.0


def test_social_score_never_rescues_technical_gate_failure():
    asset = {
        "_score": 60.0,
        "_technical_score": 60.0,
        "_social_score": _context(sentiment_score=100.0),
    }

    result = apply_social_score_to_asset(asset, is_futures=False, technical_threshold=65.0)

    assert result["applied"] is False
    assert result["fallback_reason"] == "technical_gate_failed"
    assert asset["_score"] == 60.0


def test_context_load_failure_preserves_technical_score_with_reason():
    asset = {"_score": 68.0, "_technical_score": 68.0}

    result = apply_social_score_to_asset(asset, is_futures=False, technical_threshold=65.0)

    assert result["applied"] is False
    assert result["fallback_reason"] == "context_unavailable"
    assert asset["_score"] == 68.0


def test_formula_clamps_extremes():
    assert confidence_adjusted_sentiment(1000.0, 1.0) == 100.0
    assert confidence_adjusted_sentiment(-1000.0, 1.0) == 0.0
    assert blend_score(1000.0, 1000.0, 0.2) == 100.0
    assert blend_score(-1000.0, -1000.0, 0.2) == 0.0


def test_social_sources_are_required_and_score_ranges_are_strict():
    with pytest.raises(ValidationError):
        SocialAssetInput.model_validate({
            "symbol": "NEAR",
            "attention_score": 101,
            "sentiment_score": 50,
            "confidence": 1,
            "sentiment_label": "neutral",
            "recommendation": "observe",
            "summary": "sample",
            "sources": [],
        })


def test_run_timestamps_require_timezone_and_reject_future_window_order():
    base = {
        "contract_version": "social-intelligence-v1",
        "external_run_id": "run-1",
        "source": "claude",
        "model": "model",
        "prompt_version": "prompt-v1",
        "window_start": "2026-08-02T12:00:00",
        "window_end": "2026-08-03T12:00:00Z",
        "collected_at": "2026-08-03T12:05:00Z",
        "assets": [{}],
    }
    with pytest.raises(ValidationError):
        SocialRunInput.model_validate(base)

    with pytest.raises(ValidationError):
        SocialRunInput.model_validate({
            **base,
            "window_start": "2026-08-03T13:00:00Z",
        })


def test_exact_freshness_boundary_is_eligible_and_one_second_after_is_stale():
    window_end = datetime(2026, 8, 2, 12, tzinfo=timezone.utc)
    observation = SimpleNamespace(
        id="observation-id",
        run_id="run-id",
        symbol="NEAR_USDT",
        attention_score=65.0,
        sentiment_score=70.0,
        confidence=0.8,
        sentiment_label="bullish",
        recommendation="observe",
        summary="summary",
        narratives=[],
        anomalies=[],
        metrics={},
        sources=[{"platform": "X", "url": "https://example.com"}],
        contract_version="social-intelligence-v1",
        window_start=window_end - timedelta(hours=24),
        window_end=window_end,
        collected_at=window_end,
    )

    exact = serialize_observation(
        observation,
        as_of=window_end + timedelta(hours=24),
        max_age_seconds=86_400,
    )
    stale = serialize_observation(
        observation,
        as_of=window_end + timedelta(hours=24, seconds=1),
        max_age_seconds=86_400,
    )

    assert exact["eligible"] is True
    assert stale["eligible"] is False


def test_social_config_defaults_dark_with_approved_weight_and_freshness():
    config = SocialScoreConfig.model_validate({})
    assert config.enabled is False
    assert config.spot_weight == pytest.approx(0.20)
    assert config.futures_weight == pytest.approx(0.20)
    assert config.max_age_seconds == 86_400


def test_social_context_does_not_enter_ml_features_snapshot():
    decision = SimpleNamespace(metrics={
        "indicators_snapshot": {"rsi": {"value": 51.0}},
        "social_score": {"sentiment_score": 99.0},
        "technical_score": 70.0,
        "final_score": 75.8,
    })

    assert _build_features_snapshot(decision) == {"rsi": 51.0}
