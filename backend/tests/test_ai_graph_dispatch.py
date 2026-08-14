from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.models.ai_graph import (
    AI_GRAPH_DISPATCH_RESUME,
    AI_GRAPH_DISPATCH_START,
)
from app.services.ai_graph_service import AIGraphRunService, GraphAccessError
from app.tasks.ai_orchestration import (
    _acquire_run,
    _dispatch_matches,
    _guarded_queued_dispatch_spec,
    _mark_legacy_start_reconciliation_required,
    _queued_dispatch_spec,
)


BACKEND = Path(__file__).resolve().parents[1]


class _ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value

    def scalar_one(self):
        return self.value

    def scalars(self):
        return self

    def all(self):
        return list(self.value)


class _SequenceDB:
    def __init__(self, *values, get_value=None):
        self.values = list(values)
        self.get_value = get_value
        self.added = []
        self.execute_count = 0
        self.flushed = False

    async def execute(self, _statement):
        self.execute_count += 1
        value = self.values.pop(0) if self.values else None
        return _ScalarResult(value)

    async def get(self, _model, _key):
        return self.get_value

    def add(self, value):
        self.added.append(value)

    async def flush(self):
        self.flushed = True


def _run(*, kind: str, interrupt_id=None, decision_id=None, status: str = "QUEUED"):
    return SimpleNamespace(
        id=uuid4(),
        status=status,
        dispatch_kind=kind,
        dispatch_interrupt_id=interrupt_id,
        dispatch_decision_id=decision_id,
    )


def test_fresh_start_delivery_cannot_consume_a_persisted_resume():
    run = _run(kind=AI_GRAPH_DISPATCH_RESUME, interrupt_id=uuid4(), decision_id=uuid4())

    assert not _dispatch_matches(run, dispatch_kind=AI_GRAPH_DISPATCH_START)


def test_old_resume_redelivery_is_noop_after_next_human_gate():
    first_interrupt_id = uuid4()
    first_decision_id = uuid4()
    second_interrupt_id = uuid4()
    second_decision_id = uuid4()
    run = _run(
        kind=AI_GRAPH_DISPATCH_RESUME,
        interrupt_id=second_interrupt_id,
        decision_id=second_decision_id,
    )

    assert not _dispatch_matches(
        run,
        dispatch_kind=AI_GRAPH_DISPATCH_RESUME,
        interrupt_id=first_interrupt_id,
        decision_id=first_decision_id,
    )
    assert not _dispatch_matches(
        run,
        dispatch_kind=AI_GRAPH_DISPATCH_RESUME,
        interrupt_id=second_interrupt_id,
        decision_id=first_decision_id,
    )
    assert _dispatch_matches(
        run,
        dispatch_kind=AI_GRAPH_DISPATCH_RESUME,
        interrupt_id=second_interrupt_id,
        decision_id=second_decision_id,
    )


def test_running_duplicate_delivery_is_noop():
    interrupt_id = uuid4()
    decision_id = uuid4()
    run = _run(
        kind=AI_GRAPH_DISPATCH_RESUME,
        interrupt_id=interrupt_id,
        decision_id=decision_id,
        status="RUNNING",
    )

    assert not _dispatch_matches(
        run,
        dispatch_kind=AI_GRAPH_DISPATCH_RESUME,
        interrupt_id=interrupt_id,
        decision_id=decision_id,
    )


def test_dispatcher_reconstructs_the_exact_persisted_resume():
    interrupt_id = uuid4()
    decision_id = uuid4()
    run = _run(
        kind=AI_GRAPH_DISPATCH_RESUME,
        interrupt_id=interrupt_id,
        decision_id=decision_id,
    )

    spec = _queued_dispatch_spec(run)

    assert spec is not None
    assert spec["task_name"] == "app.tasks.ai_orchestration.resume_graph_run"
    assert spec["args"] == (str(run.id), str(interrupt_id), str(decision_id))
    assert str(interrupt_id) in str(spec["dedup_key"])
    assert str(decision_id) in str(spec["dedup_key"])


def test_dispatch_schema_aborts_on_legacy_resume_and_enforces_pair():
    migration = (
        BACKEND / "alembic" / "versions" / "167_chat_concrete_proposal_paths.py"
    ).read_text(encoding="utf-8")
    service = (BACKEND / "app" / "services" / "ai_graph_service.py").read_text(
        encoding="utf-8"
    )

    assert "ANALYSIS_CHAT_LEGACY_RESUME_RECONCILIATION_REQUIRED" in migration
    assert "run.status IN ('QUEUED', 'RUNNING')" in migration
    assert "interrupt.status IN ('RESOLVED', 'REJECTED')" in migration
    assert "interrupt.decision_id IS NOT NULL" not in migration
    assert "SET dispatch_kind = 'RESUME'" not in migration
    assert "RAISE EXCEPTION" in migration
    assert "ck_ai_graph_run_dispatch_payload" in migration
    assert 'run.dispatch_kind = AI_GRAPH_DISPATCH_RESUME' in service
    assert "run.dispatch_interrupt_id = interrupt_id" in service
    assert "run.dispatch_decision_id = decision_id" in service


def test_resume_delivery_is_acked_on_receipt_and_recovered_from_durable_dispatch():
    celery_config = (BACKEND / "app" / "tasks" / "celery_app.py").read_text(
        encoding="utf-8"
    )

    resume_routes = celery_config.split(
        '"app.tasks.ai_orchestration.resume_graph_run":', 1
    )[1]
    assert "**_NO_REQUEUE_ON_WORKER_LOSS" in resume_routes


def test_legacy_two_argument_resume_task_is_a_fail_closed_noop():
    task = (BACKEND / "app" / "tasks" / "ai_orchestration.py").read_text(
        encoding="utf-8"
    )

    assert "decision_id: str | None = None" in task
    assert "LEGACY_RESUME_TASK_FENCED" in task
    assert "Never infer a decision id from the interrupt" in task


def test_stale_running_resume_fails_closed_instead_of_replaying_human_decision():
    task = (BACKEND / "app" / "tasks" / "ai_orchestration.py").read_text(
        encoding="utf-8"
    )
    recovery = task.split(
        'def recover_stale_graph_runs() -> dict:', 1
    )[1].split(
        '@celery_app.task(name="app.tasks.ai_orchestration.cancel_graph_run")', 1
    )[0]

    assert "if run.dispatch_kind == AI_GRAPH_DISPATCH_RESUME" in recovery
    assert "await _mark_failed(" in recovery
    assert 'STALE_RESUME_RECONCILIATION_REQUIRED' in recovery
    assert "recoverable.append(run)" in recovery
    assert 'error_kind="GRAPH_RECONCILIATION_REQUIRED"' in recovery


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal_status", ["COMPLETED", "FAILED", "CANCELLED"])
async def test_resume_rejects_every_terminal_run(terminal_status):
    tenant_id = uuid4()
    run = SimpleNamespace(id=uuid4(), tenant_id=tenant_id, status=terminal_status)
    db = _SequenceDB(run)

    with pytest.raises(GraphAccessError, match="GRAPH_RUN_TERMINAL"):
        await AIGraphRunService.resume(
            db,
            tenant_id=tenant_id,
            actor_user_id=tenant_id,
            run_id=run.id,
            interrupt_id=uuid4(),
            decision="approve",
            decision_id=uuid4(),
            idempotency_key="resume-terminal-rejected",
            edits={},
        )
    assert db.execute_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("run_status", "interrupt_type"),
    [
        ("INTERRUPTED", "PROPOSAL_CONFIRMATION"),
        ("WAITING_SHADOW", "SHADOW_EVIDENCE"),
    ],
)
async def test_resume_accepts_only_the_status_matching_the_interrupt(
    run_status, interrupt_type,
):
    tenant_id = uuid4()
    run_id = uuid4()
    interrupt_id = uuid4()
    decision_id = uuid4()
    run = SimpleNamespace(
        id=run_id,
        tenant_id=tenant_id,
        status=run_status,
        dispatch_kind=AI_GRAPH_DISPATCH_START,
        dispatch_interrupt_id=None,
        dispatch_decision_id=None,
        updated_at=None,
    )
    interrupt = SimpleNamespace(
        id=interrupt_id,
        tenant_id=tenant_id,
        graph_run_id=run_id,
        interrupt_type=interrupt_type,
        idempotency_key=None,
        decision_id=None,
        status="PENDING",
        allowed_edit_fields=[],
    )
    db = _SequenceDB(run, interrupt)

    resumed, reused, persisted_decision_id = await AIGraphRunService.resume(
        db,
        tenant_id=tenant_id,
        actor_user_id=tenant_id,
        run_id=run_id,
        interrupt_id=interrupt_id,
        decision="approve",
        decision_id=decision_id,
        idempotency_key="matching-interrupt-state",
        edits={},
    )

    assert resumed.status == "QUEUED"
    assert reused is False
    assert persisted_decision_id == decision_id
    assert resumed.dispatch_interrupt_id == interrupt_id
    assert resumed.dispatch_decision_id == decision_id
    assert db.flushed is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("run_status", "interrupt_type"),
    [
        ("WAITING_SHADOW", "PROPOSAL_CONFIRMATION"),
        ("INTERRUPTED", "SHADOW_EVIDENCE"),
        ("RUNNING", "PROPOSAL_CONFIRMATION"),
    ],
)
async def test_resume_rejects_a_run_interrupt_state_mismatch(
    run_status, interrupt_type,
):
    tenant_id = uuid4()
    run_id = uuid4()
    interrupt_id = uuid4()
    run = SimpleNamespace(id=run_id, tenant_id=tenant_id, status=run_status)
    interrupt = SimpleNamespace(
        id=interrupt_id,
        tenant_id=tenant_id,
        graph_run_id=run_id,
        interrupt_type=interrupt_type,
        idempotency_key=None,
        status="PENDING",
    )
    db = _SequenceDB(run, interrupt)

    with pytest.raises(
        GraphAccessError, match="GRAPH_RUN_INTERRUPT_STATE_MISMATCH"
    ):
        await AIGraphRunService.resume(
            db,
            tenant_id=tenant_id,
            actor_user_id=tenant_id,
            run_id=run_id,
            interrupt_id=interrupt_id,
            decision="approve",
            decision_id=uuid4(),
            idempotency_key="mismatched-interrupt-state",
            edits={},
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal_status", ["COMPLETED", "FAILED", "CANCELLED"])
async def test_terminal_markers_never_regress_an_existing_terminal_run(
    terminal_status,
):
    from app.tasks.ai_orchestration import (
        _mark_failed,
        _mark_interrupted,
        _mark_terminal,
    )

    interrupt = SimpleNamespace(
        id=uuid4(), value={"interrupt_type": "PROPOSAL_CONFIRMATION"}
    )
    calls = (
        lambda db, run: _mark_terminal(db, run.id, {"current_node": "complete"}),
        lambda db, run: _mark_interrupted(db, run.id, interrupt),
        lambda db, run: _mark_failed(
            db,
            run.id,
            failed_node="invoke_provider",
            error_kind="GRAPH_EXECUTION_FAILED",
            reason_code="LATE_FAILURE",
            safe_message="late failure",
            provider_transport_attempted=True,
            terminal_reason="FAIL_CLOSED",
        ),
    )
    for call in calls:
        run = SimpleNamespace(id=uuid4(), status=terminal_status)
        db = _SequenceDB(run)
        await call(db, run)
        assert run.status == terminal_status
        assert db.execute_count == 1


@pytest.mark.asyncio
async def test_mark_terminal_preserves_audited_provider_transport():
    from app.tasks.ai_orchestration import _mark_terminal

    run = SimpleNamespace(
        id=uuid4(),
        status="RUNNING",
        tenant_id=uuid4(),
        ai_request_id=uuid4(),
        ai_job_id=None,
        provider_transport_attempted=True,
        current_node="complete_message",
        last_completed_node="persist_message_result_usage",
    )
    db = _SequenceDB(run, None, get_value=None)

    await _mark_terminal(
        db,
        run.id,
        {"current_node": "complete_message", "terminal_reason": "COMPLETED"},
    )

    assert run.status == "COMPLETED"
    assert run.provider_transport_attempted is True


@pytest.mark.asyncio
@pytest.mark.parametrize("run_status", ["QUEUED", "RUNNING"])
@pytest.mark.parametrize("interrupt_status", ["PENDING", "RESOLVED", "REJECTED"])
async def test_legacy_start_with_any_human_interrupt_history_is_fenced(
    monkeypatch, run_status, interrupt_status,
):
    from app.tasks import ai_orchestration as task_module

    run = _run(kind=AI_GRAPH_DISPATCH_START, status=run_status)
    run.tenant_id = uuid4()
    db = _SequenceDB(interrupt_status)
    marked = []

    async def _mark(_db, marked_run):
        marked.append(marked_run.id)

    monkeypatch.setattr(
        task_module, "_mark_legacy_start_reconciliation_required", _mark
    )
    spec, fenced = await _guarded_queued_dispatch_spec(db, run)

    assert spec is None
    assert fenced is True
    assert marked == [run.id]


@pytest.mark.asyncio
@pytest.mark.parametrize("interrupt_status", ["RESOLVED", "REJECTED"])
async def test_worker_acquire_never_executes_legacy_start_after_a_decision(
    monkeypatch, interrupt_status,
):
    from app.tasks import ai_orchestration as task_module

    run = _run(kind=AI_GRAPH_DISPATCH_START, status="QUEUED")
    run.tenant_id = uuid4()
    db = _SequenceDB(run, interrupt_status)
    marked = []

    async def _mark(_db, marked_run):
        marked.append(marked_run.id)

    monkeypatch.setattr(
        task_module, "_mark_legacy_start_reconciliation_required", _mark
    )
    acquired = await _acquire_run(
        db,
        run.id,
        dispatch_kind=AI_GRAPH_DISPATCH_START,
    )

    assert acquired is None
    assert marked == [run.id]


@pytest.mark.asyncio
async def test_legacy_start_fence_is_terminal_and_auditable():
    run = SimpleNamespace(
        id=uuid4(),
        status="QUEUED",
        tenant_id=uuid4(),
        ai_request_id=uuid4(),
        ai_job_id=None,
        current_node="interrupt_proposal_confirmation",
        provider_transport_attempted=False,
    )
    db = _SequenceDB(run, None, get_value=None)

    await _mark_legacy_start_reconciliation_required(db, run)

    assert run.status == "FAILED"
    assert run.error_kind == "GRAPH_RECONCILIATION_REQUIRED"
    assert (
        run.last_error_code
        == "LEGACY_START_INTERRUPT_HISTORY_RECONCILIATION_REQUIRED"
    )
    assert run.terminal_reason == "FAIL_CLOSED_RECONCILIATION_REQUIRED"
    assert run.provider_transport_attempted is False


@pytest.mark.parametrize("interrupt_status", ["RESOLVED", "REJECTED"])
def test_dispatcher_never_enqueues_legacy_start_after_a_human_decision(
    monkeypatch, interrupt_status,
):
    from app.tasks import ai_orchestration as task_module

    run = _run(kind=AI_GRAPH_DISPATCH_START, status="QUEUED")
    run.tenant_id = uuid4()
    db = _SequenceDB([run], interrupt_status)
    enqueued = []

    async def _run_db_task(fn, *, celery):
        assert celery is True
        return await fn(db)

    async def _mark(_db, marked_run):
        marked_run.status = "FAILED"

    def _enqueue(spec):
        enqueued.append(spec)
        return "unexpected-task-id"

    monkeypatch.setattr(task_module, "run_db_task", _run_db_task)
    monkeypatch.setattr(
        task_module, "_mark_legacy_start_reconciliation_required", _mark
    )
    monkeypatch.setattr(task_module, "_enqueue_dispatch", _enqueue)

    result = task_module.dispatch_queued_graph_runs.run()

    assert result == {
        "status": "COMPLETED",
        "eligible": 0,
        "dispatched": 0,
        "reconciliation_required": 1,
    }
    assert run.status == "FAILED"
    assert enqueued == []
