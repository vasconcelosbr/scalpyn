"""Auditoria 2026-09-18 — retenção de indicators (13GB, 41% do banco, mesmo
padrão de risco que causou o crash de disco do Postgres em 2026-07-26 via
indicator_snapshots). Task apaga em lotes; nunca deve propagar exceção para
o beat (mesma regra das outras tasks de manutenção)."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.tasks import prune_indicators as mod


def _session_cm(db):
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=db)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


def _result(rowcount):
    r = MagicMock()
    r.rowcount = rowcount
    return r


@pytest.mark.asyncio
async def test_prune_stops_after_partial_batch():
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_result(mod.BATCH_SIZE - 1))
    with patch("app.database.get_celery_session", return_value=_session_cm(db)):
        result = await mod._prune()

    assert result["total_deleted"] == mod.BATCH_SIZE - 1
    assert result["batches"] == 1
    assert result["hit_batch_cap"] is False
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_prune_loops_across_full_batches_until_exhausted():
    db = AsyncMock()
    db.execute = AsyncMock(
        side_effect=[_result(mod.BATCH_SIZE), _result(mod.BATCH_SIZE), _result(42)]
    )
    with patch("app.database.get_celery_session", return_value=_session_cm(db)):
        result = await mod._prune()

    assert result["batches"] == 3
    assert result["total_deleted"] == mod.BATCH_SIZE * 2 + 42
    assert result["hit_batch_cap"] is False


@pytest.mark.asyncio
async def test_prune_respects_max_batches_safety_cap(monkeypatch):
    monkeypatch.setattr(mod, "MAX_BATCHES_PER_RUN", 2)
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_result(mod.BATCH_SIZE))  # never a partial batch
    with patch("app.database.get_celery_session", return_value=_session_cm(db)):
        result = await mod._prune()

    assert result["batches"] == 2
    assert result["hit_batch_cap"] is True


def test_run_never_raises_when_prune_fails():
    with patch.object(mod, "_prune", return_value=None), \
         patch.object(mod, "_run_async", side_effect=RuntimeError("db down")):
        mod.run()  # must not raise — best-effort, same rule as other maintenance tasks


def test_run_logs_warning_when_batch_cap_hit(caplog):
    with patch.object(mod, "_prune", return_value=None), \
         patch.object(
             mod,
             "_run_async",
             return_value={
                 "total_deleted": 100,
                 "batches": 5,
                 "retention_days": 90,
                 "hit_batch_cap": True,
             },
         ):
        with caplog.at_level("WARNING"):
            mod.run()

    assert any("MAX_BATCHES_PER_RUN" in r.message for r in caplog.records)


def test_delete_query_batches_by_ctid_not_id():
    """indicators has no id column / single-column PK (only the composite
    unique index on time, symbol, timeframe) -- the batching subquery must
    use ctid, unlike prune_indicator_snapshots' id-based one."""
    import inspect
    source = inspect.getsource(mod._prune)
    assert "ctid IN" in source
    assert "SELECT ctid FROM indicators" in source
    assert "WHERE id IN" not in source
