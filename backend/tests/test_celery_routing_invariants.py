"""Architectural-invariant lint tests for Celery routing (Task #216).

These tests pin the five invariants documented in
``docs/runbooks/celery-queue-topology.md`` at the source-code level:

1. ``get_merged_indicators`` is the only sanctioned indicator read path
   inside the four decision tasks.
2. Each consumer asserts ``is_complete()`` before scoring/decision.
3. No raw ``celery_app.send_task()`` / ``<task>.apply_async()`` inside
   ``app/tasks/**/*.py`` outside the dedup wrapper.
4. Every registered task name appears in
   ``celery_app.conf.task_routes`` (no implicit fallback queue).
5. Every pool-universe query in the four decision tasks includes
   ``is_approved = true``.

Failing one of these tests should block the deploy: each invariant
encodes a class of pipeline outage that cost real money in production
before Task #216.
"""

from __future__ import annotations

import ast
import inspect
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


TASKS_DIR = Path(__file__).resolve().parent.parent / "app" / "tasks"

DECISION_TASK_MODULES = (
    "app.tasks.evaluate_signals",
    "app.tasks.execute_buy",
    "app.tasks.pipeline_scan",
    "app.tasks.compute_scores",
)

# Files that legitimately call ``send_task`` / ``apply_async`` directly.
# Anything else under ``app/tasks/`` calling these primitives must route
# through ``task_dispatch.enqueue`` instead.
DISPATCH_ALLOWLIST = {"task_dispatch.py"}


def _strip_comments(src: str) -> str:
    """Remove ``#`` comments so historical mentions in commentary do not
    trigger anti-pattern asserts. Same helper used in
    ``test_execution_path_indicator_integration.py``."""
    cleaned = []
    for line in src.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        if " #" in line:
            line = line.split(" #", 1)[0]
        cleaned.append(line)
    return "\n".join(cleaned)


# ── Invariant #1: provider is the only sanctioned read path ────────────────

@pytest.mark.parametrize("module_name", DECISION_TASK_MODULES)
def test_decision_task_uses_indicators_provider(module_name: str) -> None:
    """Each decision task module must import ``get_merged_indicators``
    from ``indicators_provider``. Direct ``DISTINCT ON ... indicators``
    queries are the bug class Task #215 fixed and must not regress."""
    module = __import__(module_name, fromlist=["__name__"])
    src = inspect.getsource(module)
    code = _strip_comments(src)
    assert "from ..services.indicators_provider import" in src, (
        f"{module_name}: missing import from indicators_provider — every "
        "decision task must consume the unified provider, not raw indicators."
    )
    assert "get_merged_indicators" in code, (
        f"{module_name}: get_merged_indicators is not referenced — the "
        "provider helper is the only sanctioned indicator read path."
    )
    assert "DISTINCT ON (i.symbol)" not in code, (
        f"{module_name}: raw DISTINCT ON indicators query found — this is "
        "the Task #215 anti-pattern (microstructure-only-latest bug)."
    )


# ── Invariant #2: every consumer gates on is_complete() ────────────────────

@pytest.mark.parametrize("module_name", DECISION_TASK_MODULES)
def test_decision_task_gates_on_is_complete(module_name: str) -> None:
    """``is_complete`` must appear in the executable code (not just the
    docstring/comments) so quarantine semantics are uniform across the
    four consumers."""
    module = __import__(module_name, fromlist=["__name__"])
    src = inspect.getsource(module)
    code = _strip_comments(src)
    # pipeline_scan delegates to ``filter_incomplete_assets`` which itself
    # calls ``is_complete``; either reference satisfies the invariant.
    if module_name == "app.tasks.pipeline_scan":
        assert "is_complete" in code or "filter_incomplete_assets" in code, (
            f"{module_name}: must gate on is_complete() (directly or via "
            "filter_incomplete_assets)."
        )
    else:
        assert "is_complete" in code, (
            f"{module_name}: is_complete() gate is missing from the "
            "executable code — incomplete payloads will reach the decision body."
        )


# ── Invariant #3: no raw send_task/apply_async outside the wrapper ─────────

class _DispatchCallFinder(ast.NodeVisitor):
    """Walk an AST and collect every ``Call`` whose function attribute is
    ``send_task`` or ``apply_async``."""

    def __init__(self) -> None:
        self.found: list[tuple[int, str]] = []

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802 (ast API)
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr in ("send_task", "apply_async"):
            self.found.append((node.lineno, func.attr))
        self.generic_visit(node)


def test_no_raw_dispatch_outside_task_dispatch() -> None:
    """Walk every ``app/tasks/**/*.py`` AST. ``send_task``/``apply_async``
    calls are only allowed in the allowlisted dispatch wrapper."""
    offenders: list[str] = []
    for path in sorted(TASKS_DIR.rglob("*.py")):
        if path.name in DISPATCH_ALLOWLIST:
            continue
        if path.name.startswith("__"):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError as exc:  # pragma: no cover — defensive
            offenders.append(f"{path}: SyntaxError {exc}")
            continue
        finder = _DispatchCallFinder()
        finder.visit(tree)
        for lineno, attr in finder.found:
            offenders.append(
                f"{path.relative_to(TASKS_DIR.parent.parent)}:{lineno} — "
                f"raw .{attr}() call (use app.tasks.task_dispatch.enqueue instead)"
            )
    assert not offenders, (
        "Direct Celery dispatch in app/tasks/ is forbidden — route through "
        "app.tasks.task_dispatch.enqueue() so dedup + queue routing apply.\n  "
        + "\n  ".join(offenders)
    )


# ── Invariant #4: every registered task is routed ──────────────────────────

def _registered_task_names() -> set[str]:
    """Force-import every module listed in ``celery_app.conf.include`` so
    the registry is authoritative, then return the ``app.tasks.*`` names."""
    from app.tasks.celery_app import celery_app
    # Celery's ``include=`` is lazy — workers trigger import on boot. In
    # the test process we have to do it explicitly.
    celery_app.loader.import_default_modules()
    return {
        name for name in celery_app.tasks
        if name.startswith("app.tasks.")
    }


def test_every_registered_task_is_routed() -> None:
    """Every ``@celery_app.task(name=...)`` registered in ``app/tasks/``
    must appear in ``celery_app.conf.task_routes``. An unrouted task
    would silently land on the default queue (we point that at
    ``structural`` for safety, but the explicit invariant is that nothing
    is implicitly defaulted)."""
    from app.tasks.celery_app import TASK_ROUTES

    registered = _registered_task_names()
    routed = set(TASK_ROUTES)
    missing = sorted(registered - routed)
    assert not missing, (
        "Tasks registered with Celery but missing from TASK_ROUTES — they "
        "would land on the default queue and skip queue isolation:\n  "
        + "\n  ".join(missing)
    )


def test_no_routes_for_unknown_tasks() -> None:
    """The reverse: a route for a task name that isn't registered means
    the spec drifted from reality (rename, deletion). Catch it before
    operators chase a ghost in /api/system/celery-status."""
    from app.tasks.celery_app import TASK_ROUTES

    registered = _registered_task_names()
    extra = sorted(set(TASK_ROUTES) - registered)
    assert not extra, (
        "TASK_ROUTES contains task names that are not registered with "
        "Celery — the routing table has drifted:\n  " + "\n  ".join(extra)
    )


def test_no_silent_default_queue_fallback() -> None:
    """Operator spec part 4: there must be NO declared default queue an
    unrouted task can silently land on. The defaults are pinned to a
    sentinel name that is never declared in ``task_queues``, and
    ``task_create_missing_queues=False`` so an attempted dispatch of a
    task missing from ``TASK_ROUTES`` raises immediately rather than
    creating a ghost queue with no consumer."""
    from app.tasks.celery_app import celery_app, ALL_QUEUES

    conf = celery_app.conf
    sentinel = "__no_default__"
    assert conf.task_default_queue == sentinel, (
        f"task_default_queue must be the sentinel {sentinel!r} (got "
        f"{conf.task_default_queue!r}); pointing it at a real queue "
        "lets unrouted tasks silently land there."
    )
    assert conf.task_default_exchange == sentinel
    assert conf.task_default_routing_key == sentinel
    assert conf.task_create_missing_queues is False, (
        "task_create_missing_queues=True would let Celery auto-create "
        "the sentinel queue at first dispatch, defeating the fail-loud "
        "contract."
    )
    declared = {q.name for q in conf.task_queues}
    assert sentinel not in declared, (
        f"Sentinel {sentinel!r} must not be a declared queue."
    )
    assert declared == set(ALL_QUEUES), (
        f"Declared queues drifted from ALL_QUEUES: {declared} != "
        f"{set(ALL_QUEUES)}"
    )


# ── Invariant #5 (Task #232): split ingestion vs execution gate ────────────
#
# The pool universe is now gated by two disjoint columns:
#   * ``is_active``   — ingestion gate (collector, indicators, scoring,
#                       pipeline_scan funnel entry, WS subscription).
#   * ``is_tradable`` — execution gate. Only ``evaluate_signals`` and
#                       ``execute_buy`` are allowed to add it.
#
# The lint test below splits the four decision-task modules into the
# two groups and asserts the correct gate is present in each.

_INGESTION_DECISION_FILES = (
    "app.tasks.pipeline_scan",
    "app.tasks.compute_scores",
)
_EXECUTION_DECISION_FILES = (
    "app.tasks.evaluate_signals",
    "app.tasks.execute_buy",
)


@pytest.mark.parametrize("module_name", _INGESTION_DECISION_FILES)
def test_pool_queries_filter_is_active(module_name: str) -> None:
    """Ingestion-side decision tasks must filter on ``is_active = true``.

    They MUST NOT additionally require ``is_tradable`` — that would
    starve the funnel and the scoring engine of candidates the operator
    has not yet authorised for live trading.
    """
    module = __import__(module_name, fromlist=["__name__"])
    src = inspect.getsource(module)
    code = _strip_comments(src).lower()
    if "from pool_coins" not in code:
        return
    assert "is_active" in code, (
        f"{module_name}: queries FROM pool_coins but never references "
        "is_active — ingestion-universe filter is missing."
    )
    assert "is_active = true" in code or "is_active=true" in code, (
        f"{module_name}: pool_coins query references is_active but the "
        "exact ``is_active = true`` predicate is missing."
    )
    assert "is_tradable" not in code, (
        f"{module_name}: ingestion-side task must NOT filter on "
        "is_tradable — that gate belongs to execution-side tasks only."
    )


@pytest.mark.parametrize("module_name", _EXECUTION_DECISION_FILES)
def test_pool_queries_filter_execution_gate(module_name: str) -> None:
    """Execution-side decision tasks must filter on
    ``is_active = true AND is_tradable = true``.

    ``is_active`` alone admits symbols still pending operator trading
    authorisation onto the live critical path; ``is_tradable`` alone
    skips the ingestion check and would re-enable a paused symbol.
    Both predicates are required.
    """
    module = __import__(module_name, fromlist=["__name__"])
    src = inspect.getsource(module)
    code = _strip_comments(src).lower()
    if "from pool_coins" not in code:
        return
    assert "is_active = true" in code or "is_active=true" in code, (
        f"{module_name}: pool_coins query missing ``is_active = true``."
    )
    assert "is_tradable = true" in code or "is_tradable=true" in code, (
        f"{module_name}: execution-side task must require "
        "``is_tradable = true`` — Task #232 split execution from ingestion."
    )
