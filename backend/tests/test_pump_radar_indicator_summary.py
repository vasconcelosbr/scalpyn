"""Lote B: identity-based recovery of approval/entry anchors in build_indicator_summary.

Fixtures are synthetic unless explicitly noted as tracing to the LIT_USDT
regression case from the audit evidence. No test performs a write.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

from app.services.pump_radar_indicator_summary import build_indicator_summary

RUN_ID = uuid4()
USER_ID = uuid4()


def _event(event_id, symbol, start_at):
    return SimpleNamespace(id=event_id, run_id=RUN_ID, symbol=symbol, start_at=start_at)


def _link(link_id, event_id, approval_at, entry_at, shadow_trade_id):
    return SimpleNamespace(
        id=link_id, event_id=event_id, user_id=USER_ID,
        approval_at=approval_at, entry_at=entry_at, shadow_trade_id=shadow_trade_id,
    )


def _row(snapshot_id, event_id, snapshot_at, state, source_priority, provenance,
          indicator_id, layer, timeframe, numeric_value, text_value, source):
    return (snapshot_id, event_id, snapshot_at, state, source_priority, provenance,
            indicator_id, layer, timeframe, numeric_value, text_value, source)


def _db(events, links, rows):
    return SimpleNamespace(execute=AsyncMock(side_effect=[
        SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: events)),
        SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: links)),
        SimpleNamespace(all=lambda: rows),
    ]))


AT = datetime(2026, 9, 13, 2, 52, 0, tzinfo=timezone.utc)


# --- T01: LIT_USDT regression -------------------------------------------------

@pytest.mark.asyncio
async def test_t01_lit_usdt_regression_recovered_by_identity():
    """Traces to the audited LIT_USDT case. Approval/entry timestamps differ
    from the snapshot's own snapshot_at, so exact-match alone (pre-fix
    behaviour) finds nothing; identity match must recover it."""
    event_id = UUID("01ef23df-641a-4c41-bfb6-5da751ff16e4")
    shadow_id = uuid4()
    link_id = uuid4()
    snapshot_id = uuid4()
    snapshot_at = datetime(2026, 9, 13, 2, 52, 18, 200015, tzinfo=timezone.utc)
    approval_at = datetime(2026, 9, 13, 2, 52, 57, 649039, tzinfo=timezone.utc)
    entry_at = datetime(2026, 9, 13, 2, 52, 57, 353489, tzinfo=timezone.utc)
    event = _event(event_id, "LIT_USDT", snapshot_at - timedelta(minutes=10))
    link = _link(link_id, event_id, approval_at, entry_at, shadow_id)
    rows = [_row(snapshot_id, event_id, snapshot_at, "PASSED", "DECISION_SNAPSHOT",
                 {"shadow_trade_id": str(shadow_id), "point_in_time": True},
                 "price_change_1m_pct", "L1", "1m", Decimal("0.2953"), None, "DECISION_SNAPSHOT")]
    db = _db([event], [link], rows)
    summary = await build_indicator_summary(db, RUN_ID, [event_id], USER_ID)
    indicator = summary["indicators"][0]
    for anchor_name in ("approval", "entry"):
        samples = indicator["anchors"][anchor_name]["samples"]
        assert len(samples) == 1, anchor_name
        assert samples[0]["value"] == 0.2953
        assert samples[0]["matched_by"] == "shadow_trade_id"
        assert samples[0]["source_snapshot_id"] == str(snapshot_id)
    assert indicator["anchors"]["before"]["samples"] == []
    assert indicator["anchors"]["start"]["samples"] == []


# --- T02: same shadow_trade_id across different events ------------------------

@pytest.mark.asyncio
async def test_t02_shadow_trade_id_reused_across_events_isolated_by_composite_key():
    shadow_id = uuid4()
    event_a, event_b = uuid4(), uuid4()
    link_a = _link(uuid4(), event_a, AT + timedelta(seconds=5), AT + timedelta(seconds=5), shadow_id)
    link_b = _link(uuid4(), event_b, AT + timedelta(seconds=5), AT + timedelta(seconds=5), shadow_id)
    snap_a, snap_b = uuid4(), uuid4()
    prov = {"shadow_trade_id": str(shadow_id)}
    rows = [
        _row(snap_a, event_a, AT, "PASSED", "DECISION_SNAPSHOT", prov, "atr_pct_5m", "L1", "5m", Decimal("1.1"), None, "DECISION_SNAPSHOT"),
        _row(snap_b, event_b, AT, "PASSED", "DECISION_SNAPSHOT", prov, "atr_pct_5m", "L1", "5m", Decimal("2.2"), None, "DECISION_SNAPSHOT"),
    ]
    events = [_event(event_a, "AAA_USDT", AT - timedelta(minutes=10)), _event(event_b, "BBB_USDT", AT - timedelta(minutes=10))]
    db = _db(events, [link_a, link_b], rows)
    summary = await build_indicator_summary(db, RUN_ID, [event_a, event_b], USER_ID)
    approval = summary["indicators"][0]["anchors"]["approval"]["samples"]
    by_event = {s["event_id"]: s["value"] for s in approval}
    assert by_event[str(event_a)] == 1.1
    assert by_event[str(event_b)] == 2.2


# --- T03: multiple links per event --------------------------------------------

@pytest.mark.asyncio
async def test_t03_multiple_links_per_event_independent_resolution():
    event_id = uuid4()
    shadow_1, shadow_2 = uuid4(), uuid4()
    link_1 = _link(uuid4(), event_id, AT, AT, shadow_1)
    link_2 = _link(uuid4(), event_id, AT + timedelta(seconds=1), AT + timedelta(seconds=1), shadow_2)
    snap_1, snap_2 = uuid4(), uuid4()
    rows = [
        _row(snap_1, event_id, AT, "PASSED", "DECISION_SNAPSHOT", {"shadow_trade_id": str(shadow_1)}, "atr_pct_5m", "L1", "5m", Decimal("1.0"), None, "DECISION_SNAPSHOT"),
        _row(snap_2, event_id, AT + timedelta(seconds=1), "PASSED", "DECISION_SNAPSHOT", {"shadow_trade_id": str(shadow_2)}, "atr_pct_5m", "L1", "5m", Decimal("2.0"), None, "DECISION_SNAPSHOT"),
    ]
    db = _db([_event(event_id, "CCC_USDT", AT - timedelta(minutes=10))], [link_1, link_2], rows)
    summary = await build_indicator_summary(db, RUN_ID, [event_id], USER_ID)
    approval = summary["indicators"][0]["anchors"]["approval"]["samples"]
    by_link = {s["link_id"]: s["value"] for s in approval}
    assert by_link[str(link_1.id)] == 1.0
    assert by_link[str(link_2.id)] == 2.0


# --- T04: identity veto survives exact-timestamp fallback ---------------------

@pytest.mark.asyncio
async def test_t04_identity_veto_survives_exact_timestamp_fallback():
    event_id = uuid4()
    own_shadow, other_shadow = uuid4(), uuid4()
    link = _link(uuid4(), event_id, AT, AT, own_shadow)
    other_snapshot = uuid4()
    # A DECISION_SNAPSHOT belonging to a different trade lands exactly at the anchor timestamp.
    rows = [
        _row(other_snapshot, event_id, AT, "PASSED", "DECISION_SNAPSHOT", {"shadow_trade_id": str(other_shadow)}, "atr_pct_5m", "L1", "5m", Decimal("9.9"), None, "DECISION_SNAPSHOT"),
    ]
    db = _db([_event(event_id, "DDD_USDT", AT - timedelta(minutes=10))], [link], rows)
    summary = await build_indicator_summary(db, RUN_ID, [event_id], USER_ID)
    approval = summary["indicators"][0]["anchors"]["approval"]["samples"]
    assert approval == []  # vetoed, not silently accepted despite exact timestamp match


# --- T05: identity snapshot after the anchor is not used ----------------------

@pytest.mark.asyncio
async def test_t05_identity_snapshot_after_anchor_not_used():
    """Synthetic: snapshot_at is after approval_at but before entry_at -- must
    resolve entry but not approval, independently."""
    event_id = uuid4()
    shadow_id = uuid4()
    approval_at = AT
    entry_at = AT + timedelta(seconds=10)
    snapshot_at = AT + timedelta(seconds=5)  # between approval and entry
    link = _link(uuid4(), event_id, approval_at, entry_at, shadow_id)
    snapshot_id = uuid4()
    rows = [_row(snapshot_id, event_id, snapshot_at, "PASSED", "DECISION_SNAPSHOT", {"shadow_trade_id": str(shadow_id)}, "atr_pct_5m", "L1", "5m", Decimal("3.3"), None, "DECISION_SNAPSHOT")]
    db = _db([_event(event_id, "EEE_USDT", AT - timedelta(minutes=10))], [link], rows)
    summary = await build_indicator_summary(db, RUN_ID, [event_id], USER_ID)
    anchors = summary["indicators"][0]["anchors"]
    assert anchors["approval"]["samples"] == []
    assert len(anchors["entry"]["samples"]) == 1
    assert anchors["entry"]["samples"][0]["value"] == 3.3


# --- T06: identity + timestamp candidates from another source, priority holds -

@pytest.mark.asyncio
async def test_t06_identity_and_timestamp_candidates_resolved_by_existing_priority():
    event_id = uuid4()
    shadow_id = uuid4()
    link = _link(uuid4(), event_id, AT, AT, shadow_id)
    decision_snapshot = uuid4()
    envelope_snapshot = uuid4()
    rows = [
        # Identity candidate: DECISION_SNAPSHOT, captured slightly before the anchor.
        _row(decision_snapshot, event_id, AT - timedelta(seconds=2), "PASSED", "DECISION_SNAPSHOT", {"shadow_trade_id": str(shadow_id)}, "atr_pct_5m", "L1", "5m", Decimal("1.5"), None, "DECISION_SNAPSHOT"),
        # Competing exact-timestamp candidate from a lower-priority source.
        _row(envelope_snapshot, event_id, AT, "PASSED", "EVALUATION_ENVELOPE", {}, "atr_pct_5m", "L1", "5m", Decimal("9.0"), None, "EVALUATION_ENVELOPE"),
    ]
    db = _db([_event(event_id, "FFF_USDT", AT - timedelta(minutes=10))], [link], rows)
    summary = await build_indicator_summary(db, RUN_ID, [event_id], USER_ID)
    approval = summary["indicators"][0]["anchors"]["approval"]["samples"]
    assert len(approval) == 1
    assert approval[0]["value"] == 1.5  # DECISION_SNAPSHOT (priority 0) wins over EVALUATION_ENVELOPE (priority 1)
    assert approval[0]["matched_by"] == "shadow_trade_id"


# --- T07: missing field on the identity-winning snapshot falls back ----------

@pytest.mark.asyncio
async def test_t07_missing_field_falls_back_to_eligible_exact_reconstruction():
    event_id = uuid4()
    shadow_id = uuid4()
    link = _link(uuid4(), event_id, AT, AT, shadow_id)
    decision_snapshot = uuid4()
    reconstruction_snapshot = uuid4()
    rows = [
        # Winning identity snapshot only has a different indicator -- no row here for macd_1h.
        _row(decision_snapshot, event_id, AT, "PASSED", "DECISION_SNAPSHOT", {"shadow_trade_id": str(shadow_id)}, "atr_pct_5m", "L1", "5m", Decimal("1.0"), None, "DECISION_SNAPSHOT"),
        # Exact-timestamp OHLCV reconstruction has macd_1h.
        _row(reconstruction_snapshot, event_id, AT, "PASSED", "OHLCV_RECONSTRUCTION", {}, "macd_1h", "L2", "1h", Decimal("0.4"), None, "OHLCV_RECONSTRUCTION"),
    ]
    db = _db([_event(event_id, "GGG_USDT", AT - timedelta(minutes=10))], [link], rows)
    summary = await build_indicator_summary(db, RUN_ID, [event_id], USER_ID)
    by_indicator = {i["indicator_id"]: i for i in summary["indicators"]}
    macd_approval = by_indicator["macd_1h"]["anchors"]["approval"]["samples"]
    assert len(macd_approval) == 1
    assert macd_approval[0]["value"] == 0.4
    assert macd_approval[0]["matched_by"] == "exact_timestamp"


# --- T08: event without snapshots --------------------------------------------

@pytest.mark.asyncio
async def test_t08_event_without_snapshots_returns_empty_no_fabrication():
    event_id = uuid4()
    db = _db([_event(event_id, "HHH_USDT", AT)], [], [])
    summary = await build_indicator_summary(db, RUN_ID, [event_id], USER_ID)
    assert summary["indicators"] == []
    assert summary["event_count"] == 1
    assert summary["link_count"] == 0


# --- T09: UUID/string identity equivalence ------------------------------------

@pytest.mark.asyncio
async def test_t09_uuid_and_string_identity_equivalence():
    event_id = uuid4()
    shadow_id = uuid4()
    link = _link(uuid4(), event_id, AT, AT, shadow_id)  # UUID object
    snapshot_id = uuid4()
    rows = [_row(snapshot_id, event_id, AT - timedelta(seconds=1), "PASSED", "DECISION_SNAPSHOT",
                 {"shadow_trade_id": str(shadow_id).upper()},  # string, different case
                 "atr_pct_5m", "L1", "5m", Decimal("1.0"), None, "DECISION_SNAPSHOT")]
    db = _db([_event(event_id, "III_USDT", AT - timedelta(minutes=10))], [link], rows)
    summary = await build_indicator_summary(db, RUN_ID, [event_id], USER_ID)
    approval = summary["indicators"][0]["anchors"]["approval"]["samples"]
    assert len(approval) == 1
    assert approval[0]["matched_by"] == "shadow_trade_id"


# --- T10: deterministic regardless of row order -------------------------------

@pytest.mark.asyncio
async def test_t10_deterministic_regardless_of_row_order():
    event_id = uuid4()
    shadow_id = uuid4()
    link = _link(uuid4(), event_id, AT, AT, shadow_id)
    snap_x, snap_y = uuid4(), uuid4()
    # Two distinct DECISION_SNAPSHOT versions with the same identity, no persisted
    # reference distinguishing them -- must be ambiguous regardless of row order.
    row_x = _row(snap_x, event_id, AT - timedelta(seconds=1), "PASSED", "DECISION_SNAPSHOT", {"shadow_trade_id": str(shadow_id)}, "atr_pct_5m", "L1", "5m", Decimal("1.0"), None, "DECISION_SNAPSHOT")
    row_y = _row(snap_y, event_id, AT - timedelta(seconds=2), "PASSED", "DECISION_SNAPSHOT", {"shadow_trade_id": str(shadow_id)}, "atr_pct_5m", "L1", "5m", Decimal("2.0"), None, "DECISION_SNAPSHOT")
    event = _event(event_id, "JJJ_USDT", AT - timedelta(minutes=10))
    db1 = _db([event], [link], [row_x, row_y])
    db2 = _db([event], [link], [row_y, row_x])
    summary1 = await build_indicator_summary(db1, RUN_ID, [event_id], USER_ID)
    summary2 = await build_indicator_summary(db2, RUN_ID, [event_id], USER_ID)
    approval1 = summary1["indicators"][0]["anchors"]["approval"]
    approval2 = summary2["indicators"][0]["anchors"]["approval"]
    assert approval1["samples"] == [] == approval2["samples"]
    assert approval1["ambiguous_count"] == 1 == approval2["ambiguous_count"]


# --- T11: read-only, never writes ---------------------------------------------

@pytest.mark.asyncio
async def test_t11_readonly_no_write_attempted():
    event_id = uuid4()
    db = SimpleNamespace(
        execute=AsyncMock(side_effect=[
            SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [_event(event_id, "KKK_USDT", AT)])),
            SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [])),
            SimpleNamespace(all=lambda: []),
        ]),
        add=AsyncMock(side_effect=AssertionError("must not write")),
        commit=AsyncMock(side_effect=AssertionError("must not commit")),
        flush=AsyncMock(side_effect=AssertionError("must not flush")),
    )
    await build_indicator_summary(db, RUN_ID, [event_id], USER_ID)
    db.add.assert_not_called()
    db.commit.assert_not_called()
    db.flush.assert_not_called()


# --- T14: legacy identity absent, exact fallback still labeled correctly -----

@pytest.mark.asyncio
async def test_t14_legacy_identity_absent_uses_labeled_exact_fallback():
    event_id = uuid4()
    link = _link(uuid4(), event_id, AT, AT, None)  # legacy link, no shadow_trade_id
    snapshot_id = uuid4()
    rows = [_row(snapshot_id, event_id, AT, "PASSED", "OHLCV_RECONSTRUCTION", {}, "atr_pct_5m", "L1", "5m", Decimal("1.0"), None, "OHLCV_RECONSTRUCTION")]
    db = _db([_event(event_id, "LLL_USDT", AT - timedelta(minutes=10))], [link], rows)
    summary = await build_indicator_summary(db, RUN_ID, [event_id], USER_ID)
    approval = summary["indicators"][0]["anchors"]["approval"]["samples"]
    assert len(approval) == 1
    assert approval[0]["matched_by"] == "exact_timestamp"


# --- T15: ambiguous versions, no latest/first pick ----------------------------

@pytest.mark.asyncio
async def test_t15_ambiguous_versions_no_latest_pick():
    event_id = uuid4()
    shadow_id = uuid4()
    link = _link(uuid4(), event_id, AT, AT, shadow_id)
    snap_older, snap_newer = uuid4(), uuid4()
    rows = [
        _row(snap_older, event_id, AT - timedelta(seconds=5), "PASSED", "DECISION_SNAPSHOT", {"shadow_trade_id": str(shadow_id)}, "atr_pct_5m", "L1", "5m", Decimal("1.0"), None, "DECISION_SNAPSHOT"),
        _row(snap_newer, event_id, AT - timedelta(seconds=1), "PASSED", "DECISION_SNAPSHOT", {"shadow_trade_id": str(shadow_id)}, "atr_pct_5m", "L1", "5m", Decimal("2.0"), None, "DECISION_SNAPSHOT"),
    ]
    db = _db([_event(event_id, "MMM_USDT", AT - timedelta(minutes=10))], [link], rows)
    summary = await build_indicator_summary(db, RUN_ID, [event_id], USER_ID)
    approval = summary["indicators"][0]["anchors"]["approval"]
    assert approval["samples"] == []
    assert approval["numeric_coverage"] == 0
    assert approval["ambiguous_count"] == 1


# --- T16: before/start invariant across multiple sources ---------------------

@pytest.mark.asyncio
async def test_t16_before_start_invariant_across_sources():
    event_id = uuid4()
    start_at = AT
    shadow_id = uuid4()
    link = _link(uuid4(), event_id, start_at + timedelta(seconds=30), start_at + timedelta(seconds=30), shadow_id)
    before_snapshot = uuid4()
    start_snapshot = uuid4()
    rows = [
        # No shadow_trade_id here: this snapshot must only be reachable via exact timestamp (before anchor).
        _row(before_snapshot, event_id, start_at - timedelta(minutes=5), "PASSED", "DECISION_SNAPSHOT", {}, "atr_pct_5m", "L1", "5m", Decimal("1.0"), None, "DECISION_SNAPSHOT"),
        _row(start_snapshot, event_id, start_at, "PASSED", "DECISION_SNAPSHOT", {"shadow_trade_id": str(shadow_id)}, "atr_pct_5m", "L1", "5m", Decimal("2.0"), None, "DECISION_SNAPSHOT"),
    ]
    db = _db([_event(event_id, "NNN_USDT", start_at)], [link], rows)
    summary = await build_indicator_summary(db, RUN_ID, [event_id], USER_ID)
    anchors = summary["indicators"][0]["anchors"]
    assert anchors["before"]["samples"][0]["value"] == 1.0
    assert anchors["start"]["samples"][0]["value"] == 2.0
    # before/start samples carry no matched_by/source_snapshot_id -- untouched shape.
    assert "matched_by" not in anchors["before"]["samples"][0]
    assert "matched_by" not in anchors["start"]["samples"][0]
    # approval/entry did not gain pre-pump values just because the link resolves later.
    assert anchors["approval"]["samples"][0]["value"] is not None


# --- T17: user scope is not leaked across an identity match ------------------

@pytest.mark.asyncio
async def test_t17_user_scope_not_leaked_across_identity_match():
    """The identity index is built only from rows already scoped to user_id in
    the SQL WHERE clause -- a cross-user snapshot never enters the candidate
    pool, regardless of a matching shadow_trade_id string."""
    event_id = uuid4()
    shadow_id = uuid4()
    link = _link(uuid4(), event_id, AT, AT, shadow_id)
    # Simulate the scoped query: only this user's snapshot exists in `rows` at all.
    rows = [_row(uuid4(), event_id, AT - timedelta(seconds=1), "PASSED", "DECISION_SNAPSHOT", {"shadow_trade_id": str(shadow_id)}, "atr_pct_5m", "L1", "5m", Decimal("1.0"), None, "DECISION_SNAPSHOT")]
    db = _db([_event(event_id, "OOO_USDT", AT - timedelta(minutes=10))], [link], rows)
    calls = []
    orig_execute = db.execute
    async def spy(stmt):
        calls.append(stmt)
        return await orig_execute(stmt)
    db.execute = spy
    await build_indicator_summary(db, RUN_ID, [event_id], USER_ID)
    value_query = str(calls[2])
    assert "user_id" in value_query


# --- T18: null/invalid identity vs legitimate 0/False -------------------------

@pytest.mark.asyncio
async def test_t18_null_invalid_and_legitimate_zero_false_distinguished():
    event_id = uuid4()
    shadow_id = uuid4()
    link = _link(uuid4(), event_id, AT, AT, shadow_id)
    snap_malformed = uuid4()
    snap_valid_zero = uuid4()
    rows = [
        # Malformed identity: must not match, must not error.
        _row(snap_malformed, event_id, AT - timedelta(seconds=1), "PASSED", "DECISION_SNAPSHOT", {"shadow_trade_id": "not-a-uuid"}, "adx_1h", "L2", "1h", Decimal("5.0"), None, "DECISION_SNAPSHOT"),
        # Legitimate numeric zero on the exact-timestamp path for a different indicator.
        _row(snap_valid_zero, event_id, AT, "PASSED", "DECISION_SNAPSHOT", {"shadow_trade_id": str(shadow_id)}, "cvd_delta_5m", "L1", "5m", Decimal("0"), None, "DECISION_SNAPSHOT"),
    ]
    db = _db([_event(event_id, "PPP_USDT", AT - timedelta(minutes=10))], [link], rows)
    summary = await build_indicator_summary(db, RUN_ID, [event_id], USER_ID)
    by_indicator = {i["indicator_id"]: i for i in summary["indicators"]}
    assert by_indicator["adx_1h"]["anchors"]["approval"]["samples"] == []
    zero_samples = by_indicator["cvd_delta_5m"]["anchors"]["approval"]["samples"]
    assert len(zero_samples) == 1
    assert zero_samples[0]["value"] == 0
    assert zero_samples[0]["value"] is not None


# --- T19: same row reachable by both paths counts once ------------------------

@pytest.mark.asyncio
async def test_t19_same_row_via_both_paths_counted_once():
    event_id = uuid4()
    shadow_id = uuid4()
    link = _link(uuid4(), event_id, AT, AT, shadow_id)
    snapshot_id = uuid4()
    # snapshot_at == anchor_at AND identity matches -- reachable via both indices.
    rows = [_row(snapshot_id, event_id, AT, "PASSED", "DECISION_SNAPSHOT", {"shadow_trade_id": str(shadow_id)}, "atr_pct_5m", "L1", "5m", Decimal("1.0"), None, "DECISION_SNAPSHOT")]
    db = _db([_event(event_id, "QQQ_USDT", AT - timedelta(minutes=10))], [link], rows)
    summary = await build_indicator_summary(db, RUN_ID, [event_id], USER_ID)
    approval = summary["indicators"][0]["anchors"]["approval"]["samples"]
    assert len(approval) == 1
    assert approval[0]["matched_by"] == "shadow_trade_id"  # identity claims it first


# --- T20: null / non-interpretable anchor never borrows the other anchor -----

@pytest.mark.asyncio
async def test_t20_null_or_noninterpretable_anchor_not_borrowed():
    event_id = uuid4()
    shadow_id = uuid4()
    link = _link(uuid4(), event_id, approval_at=None, entry_at=AT, shadow_trade_id=shadow_id)
    snapshot_id = uuid4()
    rows = [_row(snapshot_id, event_id, AT, "PASSED", "DECISION_SNAPSHOT", {"shadow_trade_id": str(shadow_id)}, "atr_pct_5m", "L1", "5m", Decimal("1.0"), None, "DECISION_SNAPSHOT")]
    db = _db([_event(event_id, "RRR_USDT", AT - timedelta(minutes=10))], [link], rows)
    summary = await build_indicator_summary(db, RUN_ID, [event_id], USER_ID)
    anchors = summary["indicators"][0]["anchors"]
    assert anchors["approval"]["samples"] == []
    assert len(anchors["entry"]["samples"]) == 1


# --- T21: resolution metadata reaches the consumer without type change -------

@pytest.mark.asyncio
async def test_t21_resolution_metadata_reaches_consumer_without_type_change():
    event_id = uuid4()
    shadow_id = uuid4()
    link = _link(uuid4(), event_id, AT, AT, shadow_id)
    snapshot_id = uuid4()
    rows = [_row(snapshot_id, event_id, AT - timedelta(seconds=1), "PASSED", "DECISION_SNAPSHOT", {"shadow_trade_id": str(shadow_id)}, "atr_pct_5m", "L1", "5m", Decimal("1.5"), None, "DECISION_SNAPSHOT")]
    db = _db([_event(event_id, "SSS_USDT", AT - timedelta(minutes=10))], [link], rows)
    summary = await build_indicator_summary(db, RUN_ID, [event_id], USER_ID)
    approval = summary["indicators"][0]["anchors"]["approval"]
    sample = approval["samples"][0]
    assert isinstance(sample["value"], float)
    assert {"matched_by", "source_snapshot_id", "source_snapshot_at"} <= sample.keys()
    # Diagnostics never inflate count/numeric_coverage beyond real samples.
    assert approval["count"] == 1
    assert approval["numeric_coverage"] == 1
