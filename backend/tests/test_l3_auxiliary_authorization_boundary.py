"""Approval must not wait for diagnostic Shadow work or its profile locks."""
import ast
import asyncio
import inspect
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.pipeline_auxiliary_work import (
    WatchlistAuxiliaryWork, run_watchlist_auxiliary_work,
)
from app.tasks import pipeline_scan


@pytest.mark.asyncio
async def test_own_session_shadow_preflight_does_not_hold_profile_lock():
    lock = asyncio.Lock()

    class Session:
        async def scalar(self, statement, parameters):
            sql = str(statement)
            assert "p.is_active IS TRUE" in sql
            assert "pw.auto_refresh IS TRUE" in sql
            if "FOR UPDATE" in sql:
                await lock.acquire()
            return "active-profile"

    # Reproduce the former wait cycle before testing the repaired preflight.
    await pipeline_scan._watchlist_profile_is_active(Session(), "watchlist")
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(lock.acquire(), timeout=0.01)
    lock.release()
    assert await pipeline_scan._watchlist_profile_is_active(
        Session(), "watchlist", lock_for_write=False,
    )
    # The final writer can acquire the same profile lock independently.
    await asyncio.wait_for(lock.acquire(), timeout=0.1)
    lock.release()


@pytest.mark.asyncio
async def test_inactive_preflight_stays_false_without_lock():
    db = SimpleNamespace(scalar=AsyncMock(return_value=None))
    assert not await pipeline_scan._watchlist_profile_is_active(
        db, "watchlist", lock_for_write=False,
    )


@pytest.mark.asyncio
async def test_authorization_drains_complete_candidates_before_slow_auxiliary(monkeypatch):
    """Execute the actual scanner stage boundary with controlled workers."""
    from app.services import l3_authorization_outbox_service as outbox

    source = inspect.getsource(pipeline_scan._run_pipeline_scan)
    tree = ast.parse(source)
    outer_session = next(n for n in tree.body[0].body if isinstance(n, ast.AsyncWith))
    body = outer_session.body
    stage_idx = next(i for i, n in enumerate(body) if isinstance(n, ast.For)
                     and isinstance(n.iter, ast.Name)
                     and n.iter.id == "_PIPELINE_EXECUTION_ORDER")
    custom_idx = next(i for i in range(stage_idx, len(body))
                      if isinstance(body[i], ast.Expr)
                      and ast.unparse(body[i]) == "await _run_stage_watchlists('custom')")
    candidates, order = [], []

    async def run_stage(stage):
        order.append(stage)
        if stage == "L3":
            # Both independent profile decision transactions must commit.
            candidates.extend(["profile-a", "profile-b"])

    async def consolidate(**kwargs):
        assert candidates == ["profile-a", "profile-b"]
        assert kwargs["scan_run_id"] == "scan"
        order.append("authorized")
        return {"processed": 2}

    async def slow_auxiliary():
        assert order[-1] == "authorized"
        order.append("auxiliary")
        await asyncio.Event().wait()

    monkeypatch.setattr(outbox, "process_l3_authorization_outbox", consolidate)
    stats = {"errors": 0}
    config = SimpleNamespace(scanner=SimpleNamespace(l3_watchlist_max_concurrency=2))
    namespace = dict(
        __package__="app.tasks", _PIPELINE_EXECUTION_ORDER=("POOL", "L1", "L2", "L3"),
        _run_stage_watchlists=run_stage, execution_id="scan", stats=stats,
        logger=pipeline_scan.logger, run_watchlist_auxiliary_work=run_watchlist_auxiliary_work,
        l3_auxiliary_jobs=[WatchlistAuxiliaryWork("a", 0.01, slow_auxiliary)],
        spot_engine_config_map={"user": config}, SpotEngineConfig=lambda: config,
        stage_buckets={"L3": [SimpleNamespace(user_id="user")]},
    )
    wrapper = ast.AsyncFunctionDef(
        name="run_boundary", args=ast.arguments(posonlyargs=[], args=[], kwonlyargs=[],
        kw_defaults=[], defaults=[]), body=body[stage_idx:custom_idx + 1], decorator_list=[],
    )
    module = ast.fix_missing_locations(ast.Module(body=[wrapper], type_ignores=[]))
    exec(compile(module, "scanner_boundary", "exec"), namespace)
    await namespace["run_boundary"]()
    assert order == ["POOL", "L1", "L2", "L3", "authorized", "auxiliary", "custom"]
    assert stats["l3_authorization_outbox_after_l3_stage"] == {"processed": 2}
    assert stats["l3_auxiliary"]["timed_out"] == 1


@pytest.mark.asyncio
async def test_auxiliary_failure_isolated_and_cancellation_cleans_up():
    cleaned = asyncio.Event()

    async def slow():
        try:
            await asyncio.Event().wait()
        finally:
            cleaned.set()

    async def broken():
        raise RuntimeError("diagnostic failure")

    done = AsyncMock()
    counts = await run_watchlist_auxiliary_work([
        WatchlistAuxiliaryWork("slow", 0.01, slow),
        WatchlistAuxiliaryWork("broken", 1, broken),
        WatchlistAuxiliaryWork("healthy", 1, done),
    ], max_concurrency=2)
    assert counts == {"completed": 1, "failed": 1, "timed_out": 1}
    assert cleaned.is_set()
    done.assert_awaited_once()


def test_diagnostic_calls_are_deferred_and_do_not_lock_parent_profile():
    source = inspect.getsource(pipeline_scan._run_pipeline_scan)
    tree = ast.parse(source)
    auxiliary = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef)
                     and n.name == "_run_l3_auxiliary")
    calls = [n for n in ast.walk(auxiliary) if isinstance(n, ast.Call)]
    checks = [n for n in calls if isinstance(n.func, ast.Name)
              and n.func.id == "_watchlist_profile_is_active"]
    assert len(checks) == 2
    assert all(any(k.arg == "lock_for_write" and k.value.value is False
                   for k in n.keywords) for n in checks)
    assert "create_l3_simulated_shadows" in ast.unparse(auxiliary)
    assert "ml_predictions" in ast.unparse(auxiliary)
    assert "_create_lab_allow" in ast.unparse(auxiliary)
    assert isinstance(auxiliary.body[0], ast.AsyncWith)
    # The final writer still locks and rechecks active status in its own TX.
    writer = (Path(pipeline_scan.__file__).parents[1] / "services/shadow_trade_service.py").read_text(encoding="utf-8")
    assert "Profile.is_active.is_(True)" in writer
    assert ".with_for_update()" in writer
