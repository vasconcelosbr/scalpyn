"""P3 Fase 1.7 (b) — heartbeat persistente JOB_ERROR.

Quando a task de certificação falha, além do log ela grava uma linha
status='JOB_ERROR' em ml_data_certification_runs (rastro visível na hora).
Best-effort e sem re-raise (nunca afeta a captura).
"""
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.tasks.ml_data_certification import _persist_job_error, _summarize_exc


def test_summarize_exc_formats_and_truncates():
    assert _summarize_exc(ValueError("boom")) == "ValueError: boom"
    long = _summarize_exc(RuntimeError("x" * 5000))
    assert len(long) <= 1000
    assert long.startswith("RuntimeError: ")


@pytest.mark.asyncio
async def test_persist_job_error_inserts_job_error_row():
    db = AsyncMock()
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=db)
    cm.__aexit__ = AsyncMock(return_value=False)
    with patch("app.database.get_celery_session", return_value=cm):
        await _persist_job_error('PostgresSyntaxError: syntax error at or near ":"')

    assert db.execute.await_count == 1
    sql = str(db.execute.await_args.args[0])
    params = db.execute.await_args.args[1]
    assert "JOB_ERROR" in sql
    assert "ml_data_certification_runs" in sql
    assert json.loads(params["inv"])["job_error"].startswith("PostgresSyntaxError")
    db.commit.assert_awaited_once()
