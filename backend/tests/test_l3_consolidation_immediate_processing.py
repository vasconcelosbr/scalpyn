"""S0.1-consolidation (2026-09-18 shadow-trade collapse fix, part 2):
confirmed via live production data that even after #173 (direct-event
immediate processing), every multi-profile L3 candidate still expired --
100% AUTHORIZATION_EXPIRED, 67-257s delays -- because consolidation only
ran after the ENTIRE scan (all stages including "custom", L3-rejected
consolidation, and the integrity check) finished, not just after the L3
watchlists that could actually contribute a candidate.

This module asserts (source inspection, matching this file's established
convention for logic embedded in the giant _run_pipeline_scan) that:
  * the per-watchlist body was extracted into a stage-parameterized helper
    (_run_stage_watchlists) so it can be invoked at two points instead of
    unconditionally covering every stage in one loop.
  * consolidation-relevant outbox processing for this scan now runs right
    after the L3-stage watchlists finish, BEFORE "custom" watchlists,
    L3-rejected consolidation, and the integrity check -- not after them.
  * the scan-end batch call and the per-watchlist direct-event immediate
    call (#173) are both still present and unscoped, so nothing regresses
    for direct events or for whatever a crash mid-cycle leaves PENDING.
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


def test_l3_stage_consolidates_before_custom_watchlists_and_rejected_lane():
    source = _pipeline_source()
    l3_stage_idx = source.index("for stage in _PIPELINE_EXECUTION_ORDER:")
    post_l3_call_idx = source.index("_l3_process_outbox_after_l3_stage", l3_stage_idx)
    custom_stage_idx = source.index('await _run_stage_watchlists("custom")', post_l3_call_idx)
    rejected_lane_idx = source.index("if l3_rejected_consolidation_candidates:", custom_stage_idx)

    # Strict ordering: L3 stage -> consolidate -> custom stage -> rejected lane.
    assert l3_stage_idx < post_l3_call_idx < custom_stage_idx < rejected_lane_idx

    between_call_and_custom = source[post_l3_call_idx:custom_stage_idx]
    assert "scan_run_id=execution_id" in between_call_and_custom
    assert "except Exception:" in between_call_and_custom


def test_run_stage_watchlists_helper_preserves_isolation_and_timeout():
    source = _pipeline_source()
    fn_idx = source.index("async def _run_stage_watchlists")
    fn_end = source.index("for stage in _PIPELINE_EXECUTION_ORDER:", fn_idx)
    body = source[fn_idx:fn_end]

    assert "asyncio.wait_for(" in body
    assert "_process_one_watchlist(wl)" in body
    assert "timeout=_wl_timeout_s" in body
    assert 'stats["errors"] += 1' in body


def test_direct_event_immediate_processing_and_scan_end_fallback_unchanged():
    """Confirms #173's per-watchlist direct-event fast path and the original
    unscoped scan-end batch call are both still present and untouched by
    this second fix.
    """
    source = _pipeline_source()
    assert "_process_direct as _l3_process_direct_now" in source
    assert "await _l3_process_direct_now(_event_id)" in source

    scan_end_idx = source.index("await process_l3_authorization_outbox(")
    snippet = source[scan_end_idx - 200: scan_end_idx + 200]
    assert "scan_run_id=execution_id" in snippet
