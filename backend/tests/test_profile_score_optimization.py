from datetime import datetime, timezone
from uuid import uuid4

from app.services.profile_intelligence_manual_service import apply_manual_action
from app.services.profile_score_optimization_service import (
    CHALLENGER_SOURCE,
    CHAMPION_SOURCE,
    ProfileScoreOptimizationService,
)
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
