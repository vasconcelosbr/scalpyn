from copy import deepcopy
from datetime import datetime, timezone
import inspect

import pytest

from app.services.l3_gate_compiler_v2 import (
    compile_conditions,
    evaluate_l3_gate_v2,
)


PROFILE = {
    "default_timeframe": "5m",
    "signals": {
        "logic": "AND",
        "conditions": [
            {"id": "score_gate", "field": "score", "operator": ">=", "value": 65},
            {"id": "taker", "field": "taker_ratio", "operator": ">=", "value": 0.52},
            {"id": "delta", "field": "volume_delta", "operator": ">", "value": 0},
            {"id": "vwap", "field": "vwap_distance_pct", "operator": "between", "min": -1, "max": 10},
        ],
    },
    "entry_triggers": {
        "logic": "AND",
        "conditions": [
            {"id": "rsi", "indicator": "rsi", "operator": "between", "min": 58, "max": 68, "required": True},
            {"id": "macd", "indicator": "macd_histogram", "operator": ">", "value": 0, "required": True},
        ],
    },
}


def _evaluate(asset, *, score=80.83, legacy="ALLOW"):
    return evaluate_l3_gate_v2(
        asset=asset,
        profile_config=PROFILE,
        score=score,
        score_context={"matched_rule_ids": ["score_liquidity"]},
        evaluated_at=datetime(2026, 8, 22, 21, 0, 42, tzinfo=timezone.utc),
        base_eligible=True,
        legacy_decision=legacy,
    )


def test_compiler_preserves_between_bounds_and_maps_score_alias():
    compiled = compile_conditions(PROFILE["signals"]["conditions"], section="signals")
    assert compiled[0]["indicator"] == "alpha_score"
    assert compiled[-1]["operator"] == "between"
    assert compiled[-1]["min"] == -1
    assert compiled[-1]["max"] == 10


def test_lit_incident_is_shadow_blocked_by_independent_signals():
    result = _evaluate({
        "symbol": "LIT_USDT",
        "indicators": {
            "taker_ratio": 0.078999,
            "volume_delta": -741.93,
            "vwap_distance_pct": 6.5658,
            "rsi": 62,
            "macd_histogram": 0.1,
        },
    })

    assert result["legacy_decision"] == "ALLOW"
    assert result["shadow_decision"] == "BLOCK"
    assert result["decision_drift"] is True
    assert result["would_authorize"] is False
    assert result["signals"]["failed"] == ["taker", "delta"]
    assert result["entry_triggers"]["gate_passed"] is True
    assert result["operational_effect"] is False
    assert result["promotion_status"] == "SHADOW_ONLY"


def test_uni_incident_is_shadow_blocked_by_entry_triggers():
    profile = deepcopy(PROFILE)
    profile["entry_triggers"]["conditions"] = [
        {"id": "bb_width", "indicator": "bb_width", "operator": "between", "min": 0.03, "max": 0.12, "required": True},
        {"id": "macd_histogram", "indicator": "macd_histogram", "operator": ">", "value": 0, "required": True},
    ]
    result = evaluate_l3_gate_v2(
        asset={
            "symbol": "UNI_USDT",
            "indicators": {
                "taker_ratio": 0.7,
                "volume_delta": 10,
                "vwap_distance_pct": 1,
                "bb_width": 0.023508,
                "macd_histogram": -0.00173363,
            },
        },
        profile_config=profile,
        score=69.17,
        score_context={},
        evaluated_at=datetime(2026, 8, 22, 15, 15, 29, tzinfo=timezone.utc),
        base_eligible=True,
        legacy_decision="ALLOW",
    )
    assert result["signals"]["gate_passed"] is True
    assert result["entry_triggers"]["failed_required"] == ["bb_width", "macd_histogram"]
    assert result["shadow_decision"] == "BLOCK"


def test_score_gates_audit_share_one_deterministic_hash():
    asset = {
        "symbol": "LIT_USDT",
        "indicators": {
            "taker_ratio": 0.6,
            "volume_delta": 20,
            "vwap_distance_pct": 1,
            "rsi": 62,
            "macd_histogram": 0.2,
        },
    }
    first = _evaluate(asset)
    second = _evaluate(deepcopy(asset))
    expected = first["evaluation_envelope_hash"]
    assert expected == second["evaluation_envelope_hash"]
    assert first["score"]["evaluation_envelope_hash"] == expected
    assert first["signals"]["evaluation_envelope_hash"] == expected
    assert first["entry_triggers"]["evaluation_envelope_hash"] == expected


def test_sections_are_mandatory_but_skipped_policy_is_only_observed():
    missing_section = deepcopy(PROFILE)
    missing_section["signals"]["conditions"] = []
    missing = evaluate_l3_gate_v2(
        asset={"symbol": "X", "indicators": {"rsi": 62, "macd_histogram": 1}},
        profile_config=missing_section,
        score=90,
        score_context={},
        evaluated_at=datetime(2026, 8, 22, tzinfo=timezone.utc),
        base_eligible=True,
        legacy_decision="ALLOW",
    )
    assert missing["signals"]["reason_codes"] == ["SECTION_NOT_CONFIGURED"]
    assert missing["would_authorize"] is False

    skipped_profile = deepcopy(PROFILE)
    skipped_profile["signals"]["conditions"] = [
        {"id": "unknown", "field": "unknown_indicator", "operator": ">", "value": 0}
    ]
    skipped = evaluate_l3_gate_v2(
        asset={"symbol": "X", "indicators": {"rsi": 62, "macd_histogram": 1}},
        profile_config=skipped_profile,
        score=90,
        score_context={},
        evaluated_at=datetime(2026, 8, 22, tzinfo=timezone.utc),
        base_eligible=True,
        legacy_decision="ALLOW",
    )
    assert skipped["signals"]["skipped"] == ["unknown"]
    assert skipped["signals"]["gate_passed"] is True


def test_no_operational_consumer_is_exposed_by_contract():
    result = _evaluate({
        "symbol": "LIT_USDT",
        "indicators": {
            "taker_ratio": 0.7,
            "volume_delta": 1,
            "vwap_distance_pct": 1,
            "rsi": 62,
            "macd_histogram": 1,
        },
    })
    assert result["operational_effect"] is False
    assert result["human_approval_required"] is True


@pytest.mark.asyncio
async def test_pipeline_dual_evaluation_keeps_legacy_authoritative(monkeypatch):
    from app.tasks import pipeline_scan

    profile = deepcopy(PROFILE)
    asset = {
        "symbol": "LIT_USDT",
        "_score": 80.83,
        "alpha_score": 80.83,
        "score": 80.83,
        "is_futures": False,
        "indicators": {
            "taker_ratio": 0.7,
            "volume_delta": 10,
            "vwap_distance_pct": 6.5658,
            "rsi": 62,
            "macd_histogram": 0.1,
        },
    }
    call_order = []

    async def fake_live(**kwargs):
        call_order.append("live")
        updated = dict(kwargs["indicators"])
        updated.update({"taker_ratio": 0.078999, "volume_delta": -741.93})
        return updated, True

    async def fake_score(assets, **kwargs):
        call_order.append("score")
        assert assets[0]["indicators"]["taker_ratio"] == 0.078999
        assets[0]["_score"] = 80.83
        assets[0]["alpha_score"] = 80.83
        assets[0]["_score_components"] = {"matched_rule_ids": ["robust_rule"]}
        return {"bucketed": 1, "robust_used": 1, "fallbacks": 0}

    monkeypatch.setattr(pipeline_scan, "_inject_live_order_flow", fake_live)
    monkeypatch.setattr(pipeline_scan, "_apply_robust_authoritative_scoring", fake_score)

    decisions = await pipeline_scan._evaluate_l3_decisions(
        [asset], profile, "L3", score_config={}, db=object(), user_id="user"
    )
    decision = decisions[0]
    gate = decision["metrics"]["l3_gate_v2"]

    assert call_order == ["live", "score"]
    assert decision["decision"] == "ALLOW"  # legacy remains authoritative
    assert decision["l3_pass"] is True
    assert gate["shadow_decision"] == "BLOCK"
    assert gate["operational_effect"] is False
    assert decision["reasons"]["_sections"]["signals"]["failed"] == ["taker", "delta"]
    assert gate["evaluation_envelope_hash"] == gate["score"]["evaluation_envelope_hash"]


@pytest.mark.asyncio
async def test_every_evaluation_is_persisted_before_edge_filtering(monkeypatch):
    from app.services import l3_gate_evaluation_store
    from app.tasks import pipeline_scan

    captured = []

    class Savepoint:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class FakeDb:
        def begin_nested(self):
            return Savepoint()

    async def fake_live(**kwargs):
        return dict(kwargs["indicators"]), True

    async def fake_score(assets, **kwargs):
        assets[0]["_score"] = 80.83
        assets[0]["alpha_score"] = 80.83
        assets[0]["_score_components"] = {"matched_rule_ids": ["robust_rule"]}
        return {"bucketed": 1, "robust_used": 1, "fallbacks": 0}

    async def fake_persist(db, decisions, **kwargs):
        captured.extend(decisions)
        assert kwargs["watchlist_id"] == "watchlist-1"
        assert kwargs["profile_id"] == "profile-1"
        return {"expected": 1, "captured": 1, "inserted": 1}

    monkeypatch.setattr(pipeline_scan, "_inject_live_order_flow", fake_live)
    monkeypatch.setattr(pipeline_scan, "_apply_robust_authoritative_scoring", fake_score)
    monkeypatch.setattr(
        l3_gate_evaluation_store,
        "persist_gate_evaluations",
        fake_persist,
    )

    decisions = await pipeline_scan._evaluate_l3_decisions(
        [{
            "symbol": "LIT_USDT",
            "_score": 80.83,
            "alpha_score": 80.83,
            "score": 80.83,
            "indicators": {
                "taker_ratio": 0.078999,
                "volume_delta": -741.93,
                "vwap_distance_pct": 6.5658,
                "rsi": 62,
                "macd_histogram": 0.1,
            },
        }],
        PROFILE,
        "L3",
        score_config={},
        db=FakeDb(),
        user_id="user-1",
        watchlist_id="watchlist-1",
        profile_id="profile-1",
        profile_name="L3_RSI_COOLDOWN_RELOAD_V1",
    )

    assert captured == decisions
    assert captured[0]["metrics"]["l3_gate_v2"]["operational_effect"] is False


@pytest.mark.asyncio
async def test_observational_failure_never_changes_legacy_decision(monkeypatch):
    from app.tasks import pipeline_scan

    async def fail_score(*args, **kwargs):
        raise RuntimeError("shadow evaluator unavailable")

    monkeypatch.setattr(pipeline_scan, "_apply_robust_authoritative_scoring", fail_score)
    decisions = await pipeline_scan._evaluate_l3_decisions(
        [{
            "symbol": "SAFE_USDT",
            "_score": 90,
            "alpha_score": 90,
            "score": 90,
            "indicators": {
                "taker_ratio": 0.7,
                "volume_delta": 1,
                "vwap_distance_pct": 1,
                "rsi": 62,
                "macd_histogram": 1,
            },
        }],
        PROFILE,
        "L3",
        score_config={},
    )
    assert decisions[0]["decision"] == "ALLOW"
    assert decisions[0]["metrics"]["l3_gate_v2"]["shadow_decision"] == "ERROR"
    assert decisions[0]["metrics"]["l3_gate_v2"]["operational_effect"] is False


def test_shadow_writer_copies_exact_gate_contract_outside_ml_features():
    from app.services import shadow_trade_service

    source = inspect.getsource(shadow_trade_service._create_from_decision)
    assert 'config_snap["l3_gate_v2"] = deepcopy(_l3_gate_v2)' in source
    assert 'features_snap["l3_gate_v2"]' not in source


def test_gate_v2_has_no_executor_or_ml_feature_consumer():
    from pathlib import Path

    backend_root = Path(__file__).resolve().parents[1] / "app"
    forbidden_roots = [backend_root / "execution", backend_root / "ml"]
    for root in forbidden_roots:
        if not root.exists():
            continue
        for path in root.rglob("*.py"):
            assert "l3_gate_v2" not in path.read_text(encoding="utf-8"), path
