from pathlib import Path


def _pipeline_source() -> str:
    return (
        Path(__file__).resolve().parents[2]
        / "backend"
        / "app"
        / "tasks"
        / "pipeline_scan.py"
    ).read_text(encoding="utf-8")


def test_ml_opportunity_ranking_insert_is_savepoint_isolated():
    source = _pipeline_source()
    start = source.index("_record_ml_opportunity_ranking")
    end = source.index("async def _ml_predict_one", start)
    snippet = source[start:end]

    assert "async with db.begin_nested()" in snippet
    assert "except Exception as _rank_exc" in snippet
    assert "transaction_rolled_back=true" in snippet


def test_watchlist_failure_is_isolated_and_does_not_abort_the_scan():
    """Each watchlist runs on its own DB session (2026-09-17 shadow-trade
    collapse fix), so a failure in one watchlist cannot leave a shared
    connection aborted for the next watchlist. This supersedes the old
    shared-session ``await db.rollback()`` dance, which is gone by design:
    an isolated per-watchlist session needs no explicit rollback to
    protect its siblings.

    2026-09-18: watchlists within a stage now run concurrently
    (``asyncio.gather`` in ``_run_stage_watchlists``), so isolation matters
    even more -- ``_run_one_watchlist_safely`` must swallow every failure
    itself (never re-raise) so one watchlist's exception cannot cancel its
    siblings still running under the same ``gather``.
    """
    source = _pipeline_source()
    fn_idx = source.index("async def _process_one_watchlist")
    fn_end = source.index("async def _run_one_watchlist_safely", fn_idx)
    fn_snippet = source[fn_idx:fn_end]
    assert "async with AsyncSessionLocal() as db:" in fn_snippet

    wrapper_idx = source.index("async def _run_one_watchlist_safely")
    wrapper_end = source.index("async def _run_stage_watchlists", wrapper_idx)
    wrapper_snippet = source[wrapper_idx:wrapper_end]
    assert "await db.rollback()" not in wrapper_snippet
    assert wrapper_snippet.count("stats[\"errors\"] += 1") == 2
    assert "raise" not in wrapper_snippet


def test_fail_closed_ml_exception_does_not_escape_as_sql_error():
    source = _pipeline_source()
    start = source.index("async def _ml_predict_one")
    end = source.index("async def _l1_predict_one", start)
    snippet = source[start:end]

    assert 'except Exception as _exc' in snippet
    assert '"reason_code": "ML_EXCEPTION_FAIL_CLOSED"' in snippet
    assert '"score_status": "ML_EXCEPTION_FAIL_CLOSED"' in snippet
    assert '"model_approved": False' in snippet
