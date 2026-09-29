"""Approved L3 rows built from the authorization contract must render in the UI.

Production 2026-09-29 (RealtimeL3, LINK_USDT approved): every trace section
showed "No rules configured" because the contract's plural section names
("filters", "block_rules", ...) never matched the UI's singular trace types,
and the untripped block rule (contract status FAIL = condition false) was
counted as a failed indicator ("TOP INDICATOR rsi_6").
"""
from app.api.watchlists import _contract_trace_item
from app.services.pipeline_rejections import build_analysis_snapshot

# Contract feature_evaluations of the approved LINK_USDT decision (22:52:15Z).
LINK_TRACE = [
    {"section": "filters", "indicator": "volume_24h", "operator": ">=",
     "expected": {"value": 400000}, "actual": 10832651.23, "status": "PASS", "reason_codes": []},
    {"section": "signals", "indicator": "market_cap", "operator": ">=",
     "expected": {"value": 10000}, "actual": 10950687367.08, "status": "PASS", "reason_codes": []},
    {"section": "entry_triggers", "indicator": "di_trend", "operator": "is_true",
     "expected": {"value": True}, "actual": True, "status": "PASS", "reason_codes": []},
    {"section": "block_rules", "indicator": "rsi_6", "operator": ">=",
     "expected": {"value": 76}, "actual": 44.83, "status": "FAIL", "reason_codes": []},
]


def test_contract_sections_map_to_the_ui_trace_types():
    types = [_contract_trace_item(item)["type"] for item in LINK_TRACE]
    assert types == ["filter", "signal", "entry_trigger", "block_rule"]


def test_untripped_block_rule_is_ok_and_not_a_failed_indicator():
    block = _contract_trace_item(LINK_TRACE[3])
    assert (block["status"], block["outcome"], block["condition_matched"]) == ("PASS", "OK", False)
    assert block["condition"] == "rsi_6 >= 76"
    assert block["expected"] == "76"
    snapshot = build_analysis_snapshot(
        symbol="LINK_USDT", stage="L3", profile_id="p", status="approved",
        trace=[_contract_trace_item(item) for item in LINK_TRACE],
        timestamp="2026-09-29T22:52:15Z",
    )
    assert snapshot["failed_indicators"] == []
    assert len(snapshot["details"]["evaluation_trace"]) == 4


def test_tripped_block_rule_and_undecidable_conditions():
    tripped = _contract_trace_item({**LINK_TRACE[3], "status": "PASS", "actual": 80.1})
    assert (tripped["status"], tripped["outcome"]) == ("FAIL", "TRIPPED")
    skipped = _contract_trace_item({**LINK_TRACE[0], "status": "CONTRACT_REJECT",
                                    "actual": None, "reason_codes": ["FEATURE_IDENTITY_NOT_AVAILABLE"]})
    assert (skipped["status"], skipped["reason"]) == ("SKIPPED", "FEATURE_IDENTITY_NOT_AVAILABLE")


def test_range_expectation_is_readable():
    item = _contract_trace_item({"section": "filters", "indicator": "rsi", "operator": "between",
                                 "expected": {"min": 50, "max": 68}, "actual": 60, "status": "PASS"})
    assert item["expected"] == "50 – 68"
