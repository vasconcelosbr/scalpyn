from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.ml.feature_contract_v2 import CAPTURE_CONTRACT_VERSION, capture_native_snapshot
from app.services.indicators_provider import build_indicators_snapshot
from app.tasks.pipeline_scan import _build_pipeline_asset, _decision_metrics
from app.utils.indicator_merge import MergedIndicators


NOW = datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc)


def test_alpha_score_timestamp_is_inherited_by_all_decision_score_fields() -> None:
    merged = MergedIndicators()
    merged.values = {"adx": 20.0}
    merged.meta = {
        "adx": {"group": "structural", "timestamp": NOW, "stale": False}
    }
    score = SimpleNamespace(
        time=NOW - timedelta(seconds=5),
        score=81,
        liquidity_score=70,
        market_structure_score=75,
        momentum_score=80,
        signal_score=85,
    )
    asset = _build_pipeline_asset(
        "BTC_USDT",
        name="BTC",
        indicators=merged.as_flat_dict(),
        score_row=score,
        has_market_metadata=True,
        price=100.0,
        price_source_at=NOW - timedelta(seconds=2),
        score_source_at=score.time,
        merged_indicators=merged,
    )
    metrics = _decision_metrics(
        asset,
        {"score": {"components": {}, "classification": "buy"}, "signal": {}},
    )

    snapshot = metrics["indicators_snapshot"]
    for key in (
        "score", "signal_score", "momentum_score", "liquidity_score",
        "market_structure_score",
    ):
        assert snapshot[key]["ts"] == "2026-08-27T11:59:55Z"
    assert metrics["price_envelope"] == {
        "value": 100.0,
        "source": "market_metadata",
        "source_at": "2026-08-27T11:59:58Z",
    }


def test_derived_ema_dependencies_preserve_oldest_and_validate_newest() -> None:
    merged = MergedIndicators()
    merged.values = {"ema9_gt_ema50": True}
    merged.meta = {
        "ema9_gt_ema50": {
            "group": "structural",
            "timestamp": NOW - timedelta(minutes=5),
            "oldest_source_at": NOW - timedelta(minutes=5),
            "newest_source_at": NOW - timedelta(minutes=1),
            "dependency_source_times": {
                "ema9": NOW - timedelta(minutes=1),
                "ema50": NOW - timedelta(minutes=5),
            },
            "stale": False,
        }
    }
    source = build_indicators_snapshot(merged, keys={"ema9_gt_ema50"})
    capture = capture_native_snapshot(
        {"ema9_gt_ema50": True},
        source_snapshot=source,
        decision_created_at=NOW,
        entry_at=NOW,
        captured_at=NOW,
    )

    assert CAPTURE_CONTRACT_VERSION == "point-in-time-v3"
    assert source["ema9_gt_ema50"]["ts"] == "2026-08-27T11:55:00+00:00"
    assert source["ema9_gt_ema50"]["newest_source_at"] == "2026-08-27T11:59:00+00:00"
    assert capture.source_times["ema9_gt_ema50"] == "2026-08-27T11:59:00+00:00"
    assert not [error for error in capture.errors if "source_timestamp" in error]


def test_future_ema_dependency_fails_closed() -> None:
    source = {
        "ema_full_alignment": {
            "value": True,
            "ts": (NOW - timedelta(minutes=2)).isoformat(),
            "dependency_source_times": {
                "ema9": (NOW + timedelta(seconds=1)).isoformat(),
                "ema50": (NOW - timedelta(minutes=2)).isoformat(),
                "ema200": (NOW - timedelta(minutes=4)).isoformat(),
            },
        }
    }
    capture = capture_native_snapshot(
        {"ema_full_alignment": True},
        source_snapshot=source,
        decision_created_at=NOW,
        entry_at=NOW,
        captured_at=NOW,
    )

    assert "feature_source_after_decision" in capture.errors
