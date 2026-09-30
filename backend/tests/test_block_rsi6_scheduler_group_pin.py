"""RSI 6 block rules under the L3 v3 resolver's pinned OHLCV scheduler_group.

Production pins ``source_policies.ohlcv.scheduler_group = "microstructure"``.
- A 5m block read the newest of compute_5m / compute_structural_5m, while the
  contract only accepts the pinned cadence: when the two were on different
  candles the contract found no candidate with the traced value and rejected
  every such asset (FEATURE_IDENTITY_NOT_AVAILABLE).
- A 1m spot RSI 6 is request-bound (single producer, tagged "structural"), so
  the pin discarded it and rejected every asset.
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.services.block_condition_timeframe import block_condition_data
from app.services.l3_authorization_contract_v3 import build_authorization_contract
from app.services.profile_engine import ProfileEngine

NOW = datetime(2026, 9, 30, 12, 0, 30, tzinfo=timezone.utc)
SCOPE = {"exchange": "gate_io", "market_type": "spot", "normalized_symbol": "TEST_USDT"}


def _resolver(profile_id="p", group="microstructure"):
    return {
        "enabled": True,
        "profile_allowlist": [profile_id],
        "policy_version": "l3_v3_provenance_resolver_v1",
        "source_policies": {"ohlcv": {
            "allowed_source_providers": ["gate.io"],
            "provider_policy_id": "spot_gate_closed_ohlcv_v1",
            "max_age_seconds": 900,
            "timeframe": "5m",
            "candle_policy": "CLOSED_ONLY",
            "scheduler_group": group,
        }},
    }


def _profile(timeframe, *, profile_id="p"):
    condition = {"id": "c-rsi6", "indicator": "rsi_6", "type": "threshold",
                 "operator": ">", "value": 76, "source": "ohlcv",
                 "source_provider": "gate.io", "provider_policy_id": "spot_gate_closed_ohlcv_v1",
                 "max_age_seconds": 900, "timeframe": timeframe, "period": 6,
                 "candle_policy": "CLOSED_ONLY"}
    return {
        "default_timeframe": "5m", "scoring": {"enabled": False},
        "filters": {"conditions": []}, "signals": {"conditions": []},
        "entry_triggers": {"conditions": []},
        "block_rules": {"blocks": [{"id": "b-rsi6", "name": "b-rsi6", "logic": "AND",
                                    "timeframe": timeframe, "conditions": [condition]}]},
        "_l3_gate_runtime_policy": {"profile_id": profile_id,
                                    "l3_v3_provenance_resolver": _resolver(profile_id)},
    }


def _row(actual, *, group, timeframe="5m", ts, **kw):
    return {"indicator": "rsi_6", "actual": actual, "group": group, "scheduler_group": group,
            "source": "ohlcv", "source_provider": "gate.io",
            "provider_policy_id": "spot_gate_closed_ohlcv_v1", "timeframe": timeframe,
            "period": 6, "parameters": {}, "candle_policy": "CLOSED_ONLY", "candle_closed": True,
            "source_timestamp": ts, "computed_at": ts + timedelta(seconds=20),
            "available_at": ts + timedelta(seconds=20), "market_scope": SCOPE,
            "age_seconds": (NOW - ts).total_seconds(), "stale": False, **kw}


def _contract(asset, profile, traced_actual):
    gate = {"filters": {"conditions": []}, "signals": {"conditions": []},
            "entry_triggers": {"conditions": []},
            "block_rules": {"evaluated": [{"id": "b-rsi6", "conditions": [
                {"condition_id": "c-rsi6", "actual": traced_actual, "status": "FAIL"}]}]}}
    return build_authorization_contract(
        asset=asset, profile_config=profile, legacy_decision="ALLOW", evaluated_at=NOW,
        profile_id="p", profile_name="P", profile_version=NOW, gate_evaluation=gate,
        runtime_policy={"l3_v3_provenance_resolver": _resolver()},
    )


def _block_actual(profile, asset):
    audit = ProfileEngine(profile).evaluate_asset(asset)["block_rules_audit"]
    return audit["rules"][0]["conditions"][0]["actual"]


def test_5m_block_reads_the_pinned_cadence_so_the_contract_resolves_it():
    older = NOW - timedelta(minutes=10)
    micro = _row(55.0, group="microstructure", ts=older)
    structural_next_candle = _row(80.0, group="structural", ts=older + timedelta(minutes=5))
    asset = {"symbol": "TEST_USDT", "indicators": {},
             "_merged_indicators": SimpleNamespace(candidates=[micro, structural_next_candle])}
    profile = _profile("5m")

    actual = _block_actual(profile, asset)
    assert actual == 55.0  # was 80.0: newest computed_at regardless of cadence

    contract = _contract(asset, profile, actual)
    block = contract["sections"]["block_rules"]["blocks"][0]["conditions"][0]
    assert block["status"] == "FAIL" and block["reason_codes"] == []
    assert block["resolved_feature"]["scheduler_group"] == "microstructure"
    assert contract["authorization_status"] == "ALLOW"


def test_block_input_is_not_pinned_without_an_allowlisted_resolver():
    micro = _row(55.0, group="microstructure", ts=NOW - timedelta(minutes=10))
    structural = _row(80.0, group="structural", ts=NOW - timedelta(minutes=5))
    asset = {"indicators": {}, "_merged_indicators": SimpleNamespace(candidates=[micro, structural])}
    condition = _profile("5m")["block_rules"]["blocks"][0]["conditions"][0]
    assert block_condition_data(condition, asset)["rsi_6"] == 80.0
    assert block_condition_data(condition, asset, scheduler_group="microstructure")["rsi_6"] == 55.0
    profile = _profile("5m", profile_id="other")
    profile["_l3_gate_runtime_policy"]["l3_v3_provenance_resolver"]["profile_allowlist"] = ["p"]
    assert _block_actual(profile, asset) == 80.0


def test_request_bound_1m_rsi6_is_exempt_from_the_scheduler_group_pin():
    micro_5m = _row(55.0, group="microstructure", ts=NOW - timedelta(minutes=10))
    direct_1m = _row(81.0, group="structural", timeframe="1m", ts=NOW - timedelta(seconds=90),
                     request_bound=True)
    asset = {"symbol": "TEST_USDT", "indicators": {},
             "_merged_indicators": SimpleNamespace(candidates=[micro_5m]),
             "_block_ohlcv_candidates": [direct_1m]}
    profile = _profile("1m")

    assert _block_actual(profile, asset) == 81.0
    contract = _contract(asset, profile, 81.0)
    block = contract["sections"]["block_rules"]["blocks"][0]["conditions"][0]
    assert block["status"] == "PASS" and block["reason_codes"] == []
    assert contract["authorization_status"] == "STRATEGY_BLOCK"


def test_scheduled_structural_1m_row_is_still_pinned_away():
    scheduled_1m = _row(81.0, group="structural", timeframe="1m", ts=NOW - timedelta(seconds=90))
    asset = {"symbol": "TEST_USDT", "indicators": {},
             "_merged_indicators": SimpleNamespace(candidates=[]),
             "_block_ohlcv_candidates": [scheduled_1m]}
    contract = _contract(asset, _profile("1m"), 81.0)
    block = contract["sections"]["block_rules"]["blocks"][0]["conditions"][0]
    assert block["status"] == "CONTRACT_REJECT"
    assert block["reason_codes"] == ["FEATURE_IDENTITY_NOT_AVAILABLE"]
