"""Trace context propagation across asyncio tasks.

Provides:

* ``new_trace`` / ``get_trace`` / ``bind_trace`` — lightweight helpers for
  the bare ``trace_id`` (string), useful when you only need a correlation
  id (e.g. logging filters, metric labels).
* ``TraceContext`` — full structured context (trace + symbol + pool +
  user + market + exchange) for end-to-end observability of a single
  decision flow.
* ``set_ctx`` / ``get_ctx`` — store / read the full context.
* ``get_log_extra`` — convenience helper that returns a ``dict`` ready to
  be passed as ``extra={}`` to the standard ``logging`` calls.

ContextVars are propagated automatically across ``asyncio.create_task``
boundaries (Python 3.7+) so the same trace flows through the whole
pipeline without explicit plumbing.
"""

from __future__ import annotations

import uuid
from contextvars import ContextVar
from dataclasses import dataclass, asdict
from typing import Literal, Optional


__all__ = [
    "TraceContext",
    "new_trace",
    "get_trace",
    "bind_trace",
    "set_ctx",
    "get_ctx",
    "get_log_extra",
]


# ── primitive: bare trace_id ───────────────────────────────────────────────
_trace_ctx: ContextVar[Optional[str]] = ContextVar("scalpyn_trace_id", default=None)


def new_trace() -> str:
    """Generate a fresh trace id and bind it to the current context.

    Returns the new trace_id so callers can also pass it explicitly when
    they need to (e.g. into a Celery task payload that crosses the
    process boundary, where ContextVar propagation does not apply).
    """
    trace_id = uuid.uuid4().hex
    _trace_ctx.set(trace_id)
    return trace_id


def get_trace() -> str:
    """Return the current trace_id, generating one lazily if absent.

    Always returns a non-empty string so log filters and metric labels
    never have to handle ``None``.
    """
    current = _trace_ctx.get()
    if current:
        return current
    return new_trace()


def bind_trace(trace_id: str) -> None:
    """Bind an externally-provided trace_id to the current context.

    Useful when the trace originates upstream (HTTP header, Celery task
    kwarg, message queue payload) and must be honored verbatim instead
    of regenerated.
    """
    _trace_ctx.set(trace_id)


# ── structured: full TraceContext ──────────────────────────────────────────
@dataclass
class TraceContext:
    """Full structured trace context for a single decision flow."""

    trace_id: str
    symbol: Optional[str] = None
    pool_id: Optional[str] = None
    user_id: Optional[str] = None
    market_type: Optional[Literal["spot", "futures"]] = None
    exchange: Optional[str] = None


_ctx_data: ContextVar[Optional[TraceContext]] = ContextVar(
    "scalpyn_trace_ctx_data", default=None
)


def set_ctx(ctx: TraceContext) -> None:
    """Store a full ``TraceContext`` in the current async context.

    Also mirrors ``ctx.trace_id`` into the bare-trace ContextVar so the
    two helpers stay in sync (callers can use either entry point).
    """
    _ctx_data.set(ctx)
    if ctx.trace_id:
        _trace_ctx.set(ctx.trace_id)


def get_ctx() -> Optional[TraceContext]:
    """Return the currently bound ``TraceContext`` (or ``None`` if unset)."""
    return _ctx_data.get()


def get_log_extra() -> dict:
    """Return a dict suitable for ``logger.info(..., extra=get_log_extra())``.

    Keys are flat (``trace_id``, ``symbol``, ``pool_id``, ``user_id``,
    ``market_type``, ``exchange``) so they can be picked up directly by
    structured log formatters (e.g. JSON / GCP Cloud Logging) without
    nested unwrapping.

    If no full ``TraceContext`` has been set but a bare ``trace_id`` is
    bound, the returned dict still contains it. Always returns at least
    ``{"trace_id": <id>}`` — never an empty dict — so downstream filters
    can rely on the key being present.
    """
    ctx = _ctx_data.get()
    if ctx is not None:
        extra = {k: v for k, v in asdict(ctx).items() if v is not None}
        extra.setdefault("trace_id", ctx.trace_id)
        return extra
    return {"trace_id": get_trace()}
