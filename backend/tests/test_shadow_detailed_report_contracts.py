from datetime import datetime, timezone
import os
import sys
from types import SimpleNamespace
from uuid import uuid4

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.api.shadow_trade_reports import (
    DetailedReportRequest,
    _normalized_filters,
    _report_completeness,
    _sha,
)
from app.services.profile_optimization_service import (
    apply_json_patch,
    apply_score_matrix_patch,
    validate_score_links,
)
from app.services.shadow_trade_analysis_service import _chunks, _extract_json
from app.tasks.shadow_trade_analysis import _count_usage_tokens


def test_report_filter_normalization_is_stable_and_persists_date_basis_by_status():
    payload = DetailedReportRequest(
        sources=["L3_REJECTED", "L3", "L3"],
        watchlist_ids=[uuid4()],
        profile_ids=[],
        outcomes=["SL_HIT", "TP_HIT"],
        date_from=datetime(2026, 7, 1, tzinfo=timezone.utc),
        date_to=datetime(2026, 8, 1, tzinfo=timezone.utc),
        timezone="America/Sao_Paulo",
    )

    filters = _normalized_filters(payload)

    assert filters["sources"] == ["L3", "L3_REJECTED"]
    assert filters["outcomes"] == ["SL_HIT", "TP_HIT"]
    assert filters["date_basis_by_status"] == {
        "OPEN": "COALESCE(entry_timestamp,created_at)",
        "TERMINAL": "COALESCE(exit_timestamp,completed_at)",
    }
    assert _sha(filters) == _sha(dict(reversed(list(filters.items()))))


def test_report_request_rejects_empty_outcomes_and_invalid_range():
    with pytest.raises(ValueError):
        DetailedReportRequest(
            sources=["L3"],
            outcomes=[],
            date_from=datetime(2026, 8, 1, tzinfo=timezone.utc),
            date_to=datetime(2026, 8, 1, tzinfo=timezone.utc),
        )


def test_report_request_defaults_to_every_operational_outcome():
    request = DetailedReportRequest(
        sources=["L3"],
        date_from=datetime(2026, 8, 1, tzinfo=timezone.utc),
        date_to=datetime(2026, 8, 2, tzinfo=timezone.utc),
    )

    assert set(request.outcomes) == {
        "TP_HIT", "SL_HIT", "TRAILING_STOP", "TIMEOUT", "OPEN"
    }


def test_report_completeness_blocks_ai_when_canonical_lineage_is_missing():
    complete = SimpleNamespace(
        watchlist_id=uuid4(), watchlist_name="L3", watchlist_level="L3",
        lineage_confidence="EXACT", lineage_source="pipeline_scan",
        lineage_resolved_at=datetime.now(timezone.utc),
        rules_snapshot={"signals": {"conditions": []}}, profile_id=uuid4(),
        features_snapshot={"rsi": 50}, features_snapshot_exit={"rsi": 45},
    )
    incomplete = SimpleNamespace(**{**complete.__dict__, "watchlist_id": None,
                                    "rules_snapshot": None})

    readiness = _report_completeness([complete, incomplete])

    assert readiness["missing_watchlist_id"] == 1
    assert readiness["missing_rules_snapshot"] == 1
    assert readiness["canonical_analysis_ready"] is False


def test_report_completeness_allows_ai_for_complete_canonical_rows():
    trade = SimpleNamespace(
        watchlist_id=uuid4(), watchlist_name="L3", watchlist_level="L3",
        lineage_confidence="EXACT", lineage_source="pipeline_scan",
        lineage_resolved_at=datetime.now(timezone.utc),
        rules_snapshot={"signals": {"conditions": []}}, profile_id=uuid4(),
        features_snapshot={"rsi": 50}, features_snapshot_exit={"rsi": 45},
    )

    assert _report_completeness([trade])["canonical_analysis_ready"] is True


def test_profile_patch_supports_all_optimization_sections_without_identity_fields():
    source = {
        "default_timeframe": "5m",
        "filters": {"conditions": []},
        "scoring": {"enabled": True, "selected_rule_ids": ["r1"]},
        "signals": {"conditions": []},
        "block_rules": {"blocks": []},
        "entry_triggers": {"conditions": []},
    }
    changes = [
        {"op": "replace", "path": "/default_timeframe", "value": "15m", "reason": "evidence"},
        {"op": "add", "path": "/filters/conditions/0", "value": {"field": "adx"}, "reason": "evidence"},
        {"op": "add", "path": "/signals/conditions/0", "value": {"field": "rsi"}, "reason": "evidence"},
        {"op": "add", "path": "/block_rules/blocks/0", "value": {"name": "liquidity", "conditions": []}, "reason": "evidence"},
        {"op": "add", "path": "/entry_triggers/conditions/0", "value": {"field": "score"}, "reason": "evidence"},
    ]

    candidate, normalized = apply_json_patch(source, changes)

    assert candidate["default_timeframe"] == "15m"
    assert candidate["block_rules"]["blocks"][0]["name"] == "liquidity"
    assert len(normalized) == len(changes)


def test_profile_patch_rejects_identity_and_version_paths():
    with pytest.raises(ValueError, match="outside optimization allowlist"):
        apply_json_patch({}, [{"op": "add", "path": "/name", "value": "renamed"}])


def test_score_link_validation_is_fail_closed():
    global_score = {
        "scoring_rules": [
            {"id": "r1", "indicator": "adx", "operator": ">=", "value": 20, "points": 4, "category": "trend"}
        ]
    }
    with pytest.raises(ValueError, match="selected_rule_ids is required"):
        validate_score_links({"scoring": {"enabled": True}}, global_score)
    with pytest.raises(ValueError, match="do not exist globally"):
        validate_score_links({"scoring": {"enabled": True, "selected_rule_ids": ["missing"]}}, global_score)
    with pytest.raises(ValueError, match="duplicate selected_rule_ids"):
        validate_score_links(
            {"scoring": {"enabled": True, "selected_rule_ids": ["r1", "r1"]}},
            global_score,
        )

    resolved = validate_score_links(
        {
            "scoring": {"enabled": True, "selected_rule_ids": ["r1"]},
            "signals": {"conditions": [{"field": "adx", "rule_id": "r1"}]},
        },
        global_score,
    )
    assert resolved["resolved_rule_ids"] == ["r1"]


def test_score_matrix_patch_requires_complete_rule_contract():
    with pytest.raises(ValueError, match="missing category"):
        apply_score_matrix_patch(
            {"scoring_rules": []},
            {"upsert_rules": [{"id": "r2", "indicator": "rsi", "operator": "<=", "points": 3}]},
        )

    candidate, diff = apply_score_matrix_patch(
        {"scoring_rules": []},
        {"upsert_rules": [{"id": "r2", "indicator": "rsi", "operator": "<=", "value": 40, "points": 3, "category": "momentum"}]},
    )
    assert candidate["scoring_rules"][0]["id"] == "r2"
    assert diff[0]["op"] == "add"


def test_structured_ai_output_and_chunking_contract(monkeypatch):
    raw = '{"summary":"ok","sample":{},"observations":[],"data_quality":[],"recommendations":[],"limitations":[]}'
    assert _extract_json(f"```json\n{raw}\n```")["summary"] == "ok"
    monkeypatch.setenv("SHADOW_ANALYSIS_MAX_INPUT_CHARS", "25")
    assert len(_chunks([{"id": "one", "value": "a" * 10}, {"id": "two", "value": "b" * 10}])) == 2


def test_provider_usage_counting_avoids_double_counting_totals():
    usage = {
        "provider_calls": [
            {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            {"input_tokens": 7, "output_tokens": 3},
            {"promptTokenCount": 4, "candidatesTokenCount": 2, "totalTokenCount": 6},
        ]
    }
    assert _count_usage_tokens(usage) == 31
