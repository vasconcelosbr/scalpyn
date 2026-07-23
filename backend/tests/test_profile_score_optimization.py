from datetime import datetime, timezone
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.services.profile_intelligence_manual_service import apply_manual_action
from app.services.profile_score_optimization_service import (
    CHALLENGER_SOURCE,
    CHAMPION_SOURCE,
    DEFAULT_POLICY,
    ProfileScoreOptimizationService,
)
from app.tasks.celery_app import QUEUE_STRUCTURAL_COMPUTE, TASK_ROUTES
from app.tasks.pipeline_scan import _RobustScoreShim
from app.tasks.shadow_trade_monitor import SHADOW_CLOSABLE_SOURCES


def test_manual_penalty_is_additive_to_robust_score_only_when_marked():
    asset = {"_score": 70, "volume_spike": 3.0}
    rule = {
        "id": "pi-volume-penalty",
        "indicator": "volume_spike",
        "operator": ">",
        "value": 2.5,
        "points": -5,
        "manual_profile_intelligence": True,
    }
    result = _RobustScoreShim(manual_rules=[rule]).compute_score(asset)
    assert result["total_score"] == 65
    assert result["manual_profile_intelligence_delta"] == -5
    assert "pi-volume-penalty" in result["matched_rules"]

    unmarked = {**rule, "manual_profile_intelligence": False}
    assert _RobustScoreShim(manual_rules=[unmarked]).compute_score(asset)["total_score"] == 70


def test_entry_trigger_actions_preserve_stable_ids():
    config = {
        "entry_triggers": {
            "logic": "AND",
            "conditions": [
                {"id": "entry-alpha", "indicator": "alpha_score", "operator": ">=", "value": 65}
            ],
        }
    }
    updated = apply_manual_action(
        config,
        "UPDATE_ENTRY_TRIGGER_THRESHOLD",
        "/entry_triggers/conditions/by_id/entry-alpha/value",
        65,
        68,
    )
    assert updated["entry_triggers"]["conditions"][0]["id"] == "entry-alpha"
    assert updated["entry_triggers"]["conditions"][0]["value"] == 68
    assert config["entry_triggers"]["conditions"][0]["value"] == 65


def test_global_quadrants_keep_rejected_tp_and_rapid_sl_separate():
    now = datetime.now(timezone.utc)
    rows = [
        {"id": uuid4(), "source": "L3", "outcome": "TP_HIT", "symbol": "A", "created_at": now},
        {"id": uuid4(), "source": "L3", "outcome": "SL_HIT", "symbol": "B", "created_at": now},
        {"id": uuid4(), "source": "L3_REJECTED", "outcome": "SL_HIT", "holding_seconds": 600, "symbol": "C", "created_at": now},
        {"id": uuid4(), "source": "L3_REJECTED", "outcome": "TP_HIT", "symbol": "D", "created_at": now},
    ]
    quadrants = ProfileScoreOptimizationService()._quadrants(rows, rapid_candles=12)
    assert quadrants["approved_tp"]["closed"] == 1
    assert quadrants["approved_sl"]["closed"] == 1
    assert quadrants["rejected_rapid_sl"]["closed"] == 1
    assert quadrants["rejected_tp"]["closed"] == 1


def test_paired_sources_are_monitorable_and_not_official_ml_sources():
    assert {CHAMPION_SOURCE, CHALLENGER_SOURCE}.issubset(SHADOW_CLOSABLE_SOURCES)
    assert CHAMPION_SOURCE not in {"L1_SPECTRUM", "L3", "L3_LAB", "L3_REJECTED"}
    assert CHALLENGER_SOURCE not in {"L1_SPECTRUM", "L3", "L3_LAB", "L3_REJECTED"}


def test_global_candidates_prepare_each_feature_snapshot_once(monkeypatch):
    import app.services.profile_score_optimization_service as module

    bucket = {
        "bucket_label": "volume_spike_gt_2_5",
        "indicator": "volume_spike",
        "condition": lambda value: value > 2.5,
        "range_min": 2.5,
        "range_max": None,
    }
    monkeypatch.setattr(module, "_get_indicator_buckets", lambda: [bucket])
    original_features = module._features
    calls = []

    def counted_features(row):
        calls.append(row["id"])
        return original_features(row)

    monkeypatch.setattr(module, "_features", counted_features)
    profile_a, profile_b = uuid4(), uuid4()
    rows = [
        {
            "id": uuid4(), "profile_id": None, "source": "L3_REJECTED",
            "outcome": "SL_HIT", "holding_seconds": 300, "pnl_pct": -1.0,
            "features_snapshot": {"volume_spike": 3.0},
        },
        {
            "id": uuid4(), "profile_id": None, "source": "L3_REJECTED",
            "outcome": "TP_HIT", "holding_seconds": 1800, "pnl_pct": 0.2,
            "features_snapshot": {"volume_spike": 3.0},
        },
        {
            "id": uuid4(), "profile_id": profile_a, "source": "L3",
            "outcome": "SL_HIT", "holding_seconds": 900, "pnl_pct": -0.5,
            "features_snapshot": {"volume_spike": 3.0},
        },
        {
            "id": uuid4(), "profile_id": profile_b, "source": "L3_LAB",
            "outcome": "TP_HIT", "holding_seconds": 900, "pnl_pct": 0.1,
            "features_snapshot": {"volume_spike": 3.0},
        },
    ]
    champions = [
        {"profile_id": profile_a, "profile_name": "A"},
        {"profile_id": profile_b, "profile_name": "B"},
    ]
    policy = {
        **DEFAULT_POLICY,
        "score_global_min_bucket_trades": 1,
        "score_global_max_changes_per_profile": 1,
    }

    candidates = ProfileScoreOptimizationService()._candidates(
        rows, champions, policy
    )

    assert len(calls) == len(rows)
    assert len(candidates) == 2
    by_profile = {item["profile_id"]: item for item in candidates}
    assert by_profile[str(profile_a)]["evidence"]["cases"] == 3
    assert by_profile[str(profile_a)]["evidence"]["sl"] == 2
    assert by_profile[str(profile_b)]["evidence"]["cases"] == 3
    assert by_profile[str(profile_b)]["evidence"]["sources"] == [
        "L3_LAB", "L3_REJECTED"
    ]


def test_global_analysis_task_is_routed_to_dedicated_compute_worker():
    route = TASK_ROUTES["app.tasks.profile_score_optimization.analyze"]
    assert route["queue"] == QUEUE_STRUCTURAL_COMPUTE


def test_global_analysis_api_returns_accepted_for_async_execution():
    from app.api.profile_intelligence import router

    route = next(
        item for item in router.routes
        if item.path.endswith("/score-intelligence/global-analysis")
    )
    assert route.status_code == 202


@pytest.mark.asyncio
async def test_queue_global_analysis_persists_only_and_never_scans_dataset():
    service = ProfileScoreOptimizationService()
    service._official_rows = AsyncMock(side_effect=AssertionError("dataset scan in API"))
    service._champions = AsyncMock(side_effect=AssertionError("champion scan in API"))

    class FakeSession:
        def __init__(self):
            self.added = None

        async def scalar(self, _statement):
            return None

        def add(self, value):
            self.added = value

        async def flush(self):
            self.added.id = uuid4()
            self.added.created_at = datetime.now(timezone.utc)

    payload, created = await service.queue_global_analysis(
        FakeSession(), uuid4(), lookback_days=30,
        idempotency_key="pi-score-test-async-queue",
    )

    assert created is True
    assert payload["status"] == "QUEUED"
    service._official_rows.assert_not_awaited()
    service._champions.assert_not_awaited()
