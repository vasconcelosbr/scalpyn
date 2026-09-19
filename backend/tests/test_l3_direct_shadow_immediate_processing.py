"""S0.1 (2026-09-17 shadow-trade collapse fix, real fix -- not the PR #167
mitigation): confirmed via live production data that even after #167 bounded
worst-case scan time, 8/8 L3 ALLOW decisions in a 2-hour window still expired
before consolidation (74-162s processing delay vs. a 60s-TTL feature,
live_trade_flow). 7 of those 8 were DIRECT events (single profile, no rival
candidates) that had no structural reason to wait for the rest of the scan's
watchlists -- only CONSOLIDATE events genuinely need every contributing
watchlist's candidate before a winner can be picked.

This module asserts (via source inspection, matching this file's existing
convention for logic embedded in the giant _run_pipeline_scan) that:
  * _persist_decision_logs exposes each outbox row's id/type on its payload
    dict as soon as it is flushed (needed by the caller to act on it).
  * The per-watchlist loop processes DIRECT events immediately after that
    watchlist's own commit (_update_last_scanned), not at scan-end.
  * CONSOLIDATE events are explicitly excluded from that immediate path,
    preserving the existing per-asset consolidation-set completion
    semantics untouched.
"""
from pathlib import Path


def _pipeline_source() -> str:
    return (
        Path(__file__).resolve().parents[2]
        / "backend"
        / "app"
        / "tasks"
        / "pipeline_scan.py"
    ).read_text(encoding="utf-8")


def test_persist_decision_logs_exposes_outbox_event_identity_per_payload():
    source = _pipeline_source()
    fn_start = source.index("async def _persist_decision_logs")
    fn_end = source.index("\n\n\n", fn_start)
    body = source[fn_start:fn_end]

    assert "outbox_by_decision_id = {}" in body
    assert "outbox_by_decision_id[row.id] = outbox_row" in body
    assert 'payload["_outbox_event_id"] = str(_outbox_row.id)' in body
    assert 'payload["_outbox_event_type"] = _outbox_row.event_type' in body


def test_direct_events_are_processed_immediately_after_watchlist_commit():
    source = _pipeline_source()
    commit_idx = source.index("_direct_event_ids = [")
    window = source[commit_idx: commit_idx + 2200]

    # Fires only for CREATE_SHADOW_IF_ALLOWED, right after this watchlist's
    # own commit -- not gated behind the scan-end batch call.
    assert '"_outbox_event_type") == "CREATE_SHADOW_IF_ALLOWED"' in window
    assert "_process_direct as _l3_process_direct_now" in window
    assert "await _l3_process_direct_now(_event_id)" in window
    # A failure here must be non-fatal -- retried by the scan-end batch/beat.
    assert "except Exception:" in window

    # CONSOLIDATE_SHADOW_IF_ALLOWED must NOT appear as a filter target in
    # this immediate-processing block -- it stays on the scan-end batch path.
    direct_block_end = window.index("Consolidated (multi-candidate) events")
    assert "CONSOLIDATE_SHADOW_IF_ALLOWED" not in window[:direct_block_end]


def test_scan_end_batch_call_is_unchanged_and_still_owns_consolidation():
    """The scan-end call must still exist, unscoped by event type, so
    CONSOLIDATE events (and any DIRECT event whose immediate attempt failed)
    keep their existing recovery path.
    """
    source = _pipeline_source()
    idx = source.index("await process_l3_authorization_outbox(")
    snippet = source[idx - 200: idx + 200]
    assert "scan_run_id=execution_id" in snippet
