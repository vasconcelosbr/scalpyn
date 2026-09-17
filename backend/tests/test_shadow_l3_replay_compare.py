"""S5 (2026-09-17 shadow-trade collapse fix): the replay-compare tool must
correctly identify, from ONE shadow's preserved evidence alone (no live
recomputation of v1, no DB), that the exact UNI_USDT/b13d595f scenario --
49.25s flow-window age + 93.73s candle delay, combining to 142.97s -- fails
v1's combined max_age_seconds=120 check but passes a v2 candidate with
flow_window_age_seconds=120.
"""
import json
from datetime import timedelta

import pytest
from test_shadow_l3_continuation import policy, T

from app.services.shadow_l3_exit_service import build_evidence
from scripts.shadow_l3_replay_compare import compare, default_candidate_v2


def _serialize(value):
    return json.loads(json.dumps(value, default=str))


def _uni_style_trade(policy):
    candles = [
        dict(time=T + timedelta(minutes=i), open=100 + i, high=102 + i, low=99 + i, close=101 + i,
             ingested_at=T + timedelta(minutes=i + 1))
        for i in range(3)
    ]
    end = T + timedelta(minutes=3)
    last_at = end - timedelta(seconds=49.247511)
    buckets = [
        dict(time=T + timedelta(minutes=i), first_at=T + timedelta(minutes=i, seconds=1),
             last_at=last_at if i == 2 else T + timedelta(minutes=i, seconds=59),
             max_gap=10, buy=8, sell=2, entry_delta=6)
        for i in range(3)
    ]
    decision_at = end + timedelta(seconds=93.725736)
    evidence = build_evidence(buckets, candles, candles, policy, T, end, decision_at)
    # Sanity: this envelope reproduces the real UNI failure under v1 before
    # it is even handed to the tool under test.
    assert evidence["quality"] == "INCOMPLETE_OR_STALE"
    envelope = {
        **evidence,
        "candle": candles[-1],
        "price_history": candles,
        "structure_history": candles,
        "flow_buckets": buckets,
        "structure_timeframe": "1m",
        "replay_lookback_seconds": 3600,
        "flow_context": {"first_at": buckets[0]["first_at"], "cvd_before_window": 0, "max_gap": 10},
        "collection_lag_seconds": (decision_at - end).total_seconds(),
    }
    return {
        "id": "uni-style", "symbol": "UNI_USDT", "entry_timestamp": T,
        "decisions": [_serialize(envelope)],
    }


def test_compare_identifies_v1_failure_and_v2_pass_on_preserved_evidence(policy):
    # alignment_seconds=120 to match the real UNI_USDT/b13d595f production
    # value (93.73s candle delay passed against it) -- the fixture's default
    # of 60s is a synthetic value unrelated to this specific scenario.
    live_policy = policy.model_copy(update={"mode": "APPLY", "alignment_seconds": 120})
    trade = _uni_style_trade(live_policy)
    frozen = {"config": live_policy.model_dump()}
    candidate = default_candidate_v2(frozen)
    assert candidate.flow_window_age_seconds == live_policy.max_age_seconds

    report = compare(trade, candidate)
    assert report["v1_incomplete_flow_evidence_at"] is not None
    assert report["v2_incomplete_flow_evidence_at"] is None
    assert report["verdict_flips"] is True
    row = report["candles"][0]
    assert row["diverges"] is True
    assert row["v1_quality"] == "INCOMPLETE_OR_STALE"
    assert row["v2_quality"] == "VALID"


def test_compare_reports_no_divergence_for_a_healthy_candle(policy):
    """Sanity: a candle with no staleness on either dimension must show
    VALID under both v1 and the default v2 candidate -- not a spurious
    divergence introduced by the comparison tool itself.
    """
    live_policy = policy.model_copy(update={"mode": "APPLY"})
    candles = [dict(time=T + timedelta(minutes=i), open=100 + i, high=102 + i, low=99 + i, close=101 + i,
                     ingested_at=T + timedelta(minutes=i + 1)) for i in range(3)]
    buckets = [dict(time=T + timedelta(minutes=i), first_at=T + timedelta(minutes=i, seconds=1),
                     last_at=T + timedelta(minutes=i, seconds=59), max_gap=10, buy=8, sell=2, entry_delta=6)
               for i in range(3)]
    end = T + timedelta(minutes=3)
    evidence = build_evidence(buckets, candles, candles, live_policy, T, end, end)
    assert evidence["quality"] == "VALID"
    envelope = {
        **evidence, "candle": candles[-1], "price_history": candles, "structure_history": candles,
        "flow_buckets": buckets, "structure_timeframe": "1m", "replay_lookback_seconds": 3600,
        "flow_context": {"first_at": buckets[0]["first_at"], "cvd_before_window": 0, "max_gap": 10},
        "collection_lag_seconds": 0.0,
    }
    trade = {"id": "healthy", "symbol": "UNI_USDT", "entry_timestamp": T, "decisions": [_serialize(envelope)]}
    frozen = {"config": {**live_policy.model_dump()}}
    candidate = default_candidate_v2(frozen)

    report = compare(trade, candidate)
    assert report["verdict_flips"] is False
    assert all(not row["diverges"] for row in report["candles"])
    assert report["candles"][0]["v2_quality"] == "VALID"
