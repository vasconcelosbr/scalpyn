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


def test_watchlist_failure_rolls_back_before_next_watchlist():
    source = _pipeline_source()
    idx = source.index('logger.exception("[PipelineScan] Error processing watchlist')
    snippet = source[idx: idx + 1200]

    assert "await db.rollback()" in snippet
    assert "continue" in snippet


def test_fail_closed_ml_exception_does_not_escape_as_sql_error():
    source = _pipeline_source()
    start = source.index("async def _ml_predict_one")
    end = source.index("async def _l1_predict_one", start)
    snippet = source[start:end]

    assert 'except Exception as _exc' in snippet
    assert '"reason_code": "ML_EXCEPTION_FAIL_CLOSED"' in snippet
    assert '"score_status": "ML_EXCEPTION_FAIL_CLOSED"' in snippet
    assert '"model_approved": False' in snippet
