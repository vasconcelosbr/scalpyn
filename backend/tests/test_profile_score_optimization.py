from datetime import datetime, timezone
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.services.profile_intelligence_manual_service import apply_manual_action
from app.services.profile_score_optimization_service import (
    AI_REPORT_SCHEMA,
    CHALLENGER_SOURCE,
    CHAMPION_SOURCE,
    DEFAULT_POLICY,
    ProfileScoreOptimizationService,
    _parse_ai_report_response,
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


def test_ai_report_schema_is_bounded_and_requires_governance_sections():
    assert AI_REPORT_SCHEMA["additionalProperties"] is False
    assert set(AI_REPORT_SCHEMA["required"]) == {
        "analysis_contract_version",
        "analysis_skill_version",
        "report_schema_version",
        "executive_summary",
        "data_quality_summary",
        "data_quality_limitation",
        "cohort_analysis",
        "confusion_matrix_interpretation",
        "confusion_matrix_operational_impact",
        "profile_decisions",
        "redundancy_summary",
        "prioritization_rationale",
        "next_steps",
    }
    decisions = AI_REPORT_SCHEMA["properties"]["profile_decisions"]
    assert "Exatamente um item" in decisions["description"]
    selected = decisions["items"]["properties"]["selected_candidate_ids"]
    assert "3" in selected["description"]


def test_ai_report_parser_accepts_structured_json_and_rejects_truncation():
    response = SimpleNamespace(
        stop_reason="end_turn",
        content=[SimpleNamespace(text=(
            '{"analysis_contract_version":"pi-ai-analysis-v2",'
            '"analysis_skill_version":"profile_intelligence_analysis_skill_v2",'
            '"executive_summary":"ok","global_diagnosis":[],'
            '"profile_recommendations":[],"risks":[],"safeguards":[]}'
        ))],
    )
    assert _parse_ai_report_response(response)["executive_summary"] == "ok"

    truncated = SimpleNamespace(
        stop_reason="max_tokens",
        content=[SimpleNamespace(text='{"executive_summary":"incomplete"')],
    )
    with pytest.raises(ValueError, match="profile_score_ai_output_truncated"):
        _parse_ai_report_response(truncated)


@pytest.mark.asyncio
async def test_ai_report_retries_once_after_output_truncation(monkeypatch):
    import app.services.profile_score_optimization_service as module

    responses = [
        SimpleNamespace(
            stop_reason="max_tokens",
            content=[SimpleNamespace(text='{"incomplete":')],
            model="claude-fable-5",
        ),
        SimpleNamespace(
            stop_reason="end_turn",
            content=[SimpleNamespace(text='{"compact":true}')],
            model="claude-fable-5",
        ),
    ]

    class FakeMessages:
        def __init__(self):
            self.calls = 0

        async def create(self, **_kwargs):
            response = responses[self.calls]
            self.calls += 1
            return response

    messages = FakeMessages()

    class FakeClient:
        def __init__(self, **_kwargs):
            self.messages = messages

        async def close(self):
            return None

    class FakeDb:
        async def scalar(self, _statement):
            return None

        async def commit(self):
            return None

    monkeypatch.setitem(
        sys.modules,
        "anthropic",
        SimpleNamespace(AsyncAnthropic=FakeClient),
    )
    monkeypatch.setattr(
        module,
        "get_decrypted_api_key",
        AsyncMock(return_value="test-key"),
    )
    monkeypatch.setattr(
        module,
        "retrieve_model_for_key",
        AsyncMock(return_value={"available": True}),
    )
    monkeypatch.setattr(
        module,
        "validate_ai_response_against_payload",
        lambda _response, _context: {
            "executive_summary": ["a", "b", "c", "d"],
            "selected_candidate_ids": [],
        },
    )

    report, provider, model, _skill_id = await ProfileScoreOptimizationService()._ai_report(
        FakeDb(),
        uuid4(),
        {},
        [],
        180,
        "claude-fable-5",
    )

    assert messages.calls == 2
    assert report["executive_summary"] == ["a", "b", "c", "d"]
    assert (provider, model) == ("anthropic", "claude-fable-5")


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
    service.policy = AsyncMock(return_value=DEFAULT_POLICY)
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
