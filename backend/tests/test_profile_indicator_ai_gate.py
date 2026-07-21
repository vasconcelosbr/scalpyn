from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.services.indicator_lift_service import _get_indicator_buckets, _validate_bucket
from app.services.profile_indicator_ai_review_service import review_indicator_adjustment
from app.services.profile_intelligence_contract import PIValidationPolicy


POLICY = PIValidationPolicy(
    min_discovery_trades=30,
    min_validation_trades=20,
    min_validation_lift=1.15,
    min_validation_winrate_delta=0.05,
    max_single_symbol_share=0.40,
    max_single_day_share=0.40,
    min_distinct_symbols=3,
    min_distinct_days=3,
    min_assoc_support_validation=0.02,
    min_assoc_confidence_validation=0.55,
    min_validation_lift_retention=0.70,
)


def _rows(*, bucket_wins: int, bucket_losses: int, base_extra_wins: int, base_extra_losses: int):
    now = datetime(2026, 7, 20, tzinfo=timezone.utc)
    rows = []
    outcomes = ["TP_HIT"] * bucket_wins + ["SL_HIT"] * bucket_losses
    for index, outcome in enumerate(outcomes):
        rows.append(SimpleNamespace(
            source="L3",
            profile_id="p1",
            outcome=outcome,
            pnl_pct=0.5 if outcome == "TP_HIT" else -0.7,
            symbol=["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"][index % 4],
            created_at=now + timedelta(days=index % 4),
            features_snapshot={"rsi": 20},
        ))
    extras = ["TP_HIT"] * base_extra_wins + ["SL_HIT"] * base_extra_losses
    for index, outcome in enumerate(extras):
        rows.append(SimpleNamespace(
            source="L3",
            profile_id="p1",
            outcome=outcome,
            pnl_pct=0.5 if outcome == "TP_HIT" else -0.7,
            symbol=["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"][index % 4],
            created_at=now + timedelta(days=index % 4),
            features_snapshot={"rsi": 50},
        ))
    return rows


def test_winner_requires_temporal_validation_then_ai_review():
    bucket = next(item for item in _get_indicator_buckets() if item["bucket_label"] == "rsi_lt_24")
    status, actionability, evidence = _validate_bucket(
        rows=_rows(bucket_wins=18, bucket_losses=2, base_extra_wins=12, base_extra_losses=28),
        source="L3",
        profile_id="p1",
        bucket_def=bucket,
        role="winning_indicator",
        discovery_cases=64,
        discovery_lift=1.39,
        losing_winrate_ratio=0.85,
        policy=POLICY,
    )
    assert status == "validated"
    assert actionability == "ai_review_pending"
    assert evidence["checks"]["directional_pnl"] is True
    assert evidence["distinct_symbols"] == 4
    assert evidence["distinct_days"] == 4


def test_loser_can_validate_for_bounded_score_penalty():
    bucket = next(item for item in _get_indicator_buckets() if item["bucket_label"] == "rsi_lt_24")
    status, actionability, evidence = _validate_bucket(
        rows=_rows(bucket_wins=3, bucket_losses=21, base_extra_wins=27, base_extra_losses=9),
        source="L3",
        profile_id="p1",
        bucket_def=bucket,
        role="losing_indicator",
        discovery_cases=41,
        discovery_lift=0.65,
        losing_winrate_ratio=0.85,
        policy=POLICY,
    )
    assert status == "validated"
    assert actionability == "ai_review_pending"
    assert evidence["negative_lift"] >= POLICY.min_validation_lift


@pytest.mark.asyncio
async def test_ai_review_approves_shadow_only_without_mutating_profile_or_dataset():
    stat = SimpleNamespace(
        id=uuid4(),
        run_id=uuid4(),
        validation_status="validated",
        actionability_status="ai_review_pending",
        role_detected="losing_indicator",
        indicator="rsi",
        bucket_label="rsi_lt_24",
        total_cases=41,
        win_rate=0.32,
        avg_pnl_pct=-0.2,
        lift_vs_base=0.65,
        evidence_json={"dataset_version": "pi-native-point-in-time-v1", "validation": {"cases": 24}},
    )
    profile = SimpleNamespace(id=uuid4(), name="L3 test", config={"signals": {}, "scoring": {}})
    response = SimpleNamespace(
        model="test-model",
        content=[SimpleNamespace(text=(
            '{"verdict":"APPROVE_SHADOW","bounded_action":"ADD_SCORE_PENALTY",'
            '"rationale":"Validation confirms a persistent negative bucket.","risks":[],"safeguards":[]}'
        ))],
    )
    client = MagicMock()
    client.messages.create = AsyncMock(return_value=response)
    client.close = AsyncMock()

    with patch(
        "app.services.profile_indicator_ai_review_service.get_decrypted_api_key",
        AsyncMock(return_value="test-key"),
    ), patch("anthropic.AsyncAnthropic", return_value=client):
        review = await review_indicator_adjustment(
            AsyncMock(), user_id=uuid4(), indicator_stat=stat, profiles=[profile]
        )

    assert review["verdict"] == "APPROVE_SHADOW"
    assert review["incumbent_mutated"] is False
    assert review["training_dataset_mutated"] is False
    assert stat.actionability_status == "validated"
    client.close.assert_awaited_once()
    prompt = client.messages.create.await_args.kwargs["messages"][0]["content"]
    assert "ADD_SCORE_PENALTY reduces the score only" in prompt
    assert "Do not invent stricter sample" in prompt


def test_ai_critic_defaults_to_48h_and_official_l1_l3_sources():
    from app.services import profile_intelligence_live_service as live

    assert live._AI_WINDOW_H == 48
    assert live._AI_DEFAULT_SOURCES == ["L1_SPECTRUM", "L3", "L3_LAB"]
    source = __import__("inspect").getsource(live.run_ai_review_cycle)
    assert "rejected_no_operating_point" not in source
    assert "ranker_only_pending_stable_regime" not in source
    assert '"ml_readiness_evaluated": False' in source
    assert '"hard_negative_patterns": "pattern_rows_not_trades"' in source
