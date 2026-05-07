"""Debug endpoint — run the 5m / structural OHLCV collector in-process.

Use case (Task #221)
--------------------

In dev the pipeline is silent: ``ohlcv`` is empty for the last 6 h,
indicators are NULL, 100 % of assets get quarantined, ``pipeline_scan``
produces ``new_signals=0`` every cycle. We need to triage whether the
problem is

  CASE A — Celery worker / beat not consuming the ``microstructure``
           queue (the collector code is fine, nobody fires it), or
  CASE B — the collector itself fails (auth, exchange API, symbol
           format, persistence, etc.).

This endpoint imports the existing private async collector functions
``_collect_5m_async`` / ``_collect_all_async`` from
``app.tasks.collect_market_data`` and runs them inside an HTTP request
so an operator can fire one manually, observe per-symbol logs in the
``Backend API`` workflow, and read a structured JSON summary including
the OHLCV row delta written during the call.

NO trading-core file is touched (no edits to ``pipeline_scan``,
``evaluate_signals``, ``execute_buy``, ``score_engine``,
``block_engine``, ``indicators_provider``, ``fetch_merged_indicators``,
or the ``indicators`` table schema).

Auth
----

Same shape as ``/api/admin/symbol-health`` (Task #167):

* ``DEBUG_COLLECT_TOKEN`` env var unset AND running on Cloud Run
  (``K_SERVICE`` set) → 404 (endpoint hidden in prod by default).
* Env unset, NOT on Cloud Run → allowed, with a WARNING log line —
  Replit/dev convenience so the operator can curl immediately.
* Env set → require ``Authorization: Bearer <token>`` OR
  ``X-Debug-Token: <token>``; mismatch → 401.

The endpoint is read-mostly from the trading core's perspective: the
only writes are exactly the writes the periodic Celery task would
already perform (``ohlcv``, ``market_metadata``).  Calling it in prod
on a healthy system is a no-op-ish (idempotent ON CONFLICT DO NOTHING).
"""

from __future__ import annotations

import asyncio
import hmac
import logging
import os
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Header, HTTPException, Query, status
from sqlalchemy import text

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/debug", tags=["debug"])

_BEARER_PREFIX = "Bearer "

# Per-symbol log markers emitted by the existing collector. Parsed by
# the in-memory log handler so we can surface a structured per-symbol
# breakdown in the JSON response without modifying the collector itself.
_RX_FETCHED = re.compile(
    r"\[COLLECT\]\[RESULT\] symbol=(?P<sym>\S+)\s+result=\S+\s+rows=(?P<rows>\S+)"
)
_RX_OK = re.compile(r"\[COLLECT\]\[OK\] symbol=(?P<sym>\S+)")
_RX_FAILED = re.compile(r"\[FAILED symbol=(?P<sym>\S+)\] timeframe=\S+ error=(?P<err>.+)$")
_RX_EMPTY = re.compile(
    r"\[COLLECT\]\[EMPTY\] symbol=(?P<sym>\S+) timeframe=\S+ reason=(?P<reason>\S+)"
)


# ─── Auth gate ──────────────────────────────────────────────────────────────


def _enforce_auth(
    authorization: Optional[str],
    x_debug_token: Optional[str],
) -> None:
    """Always return 404 to unauthenticated callers (hides existence of the
    endpoint); serve normally only when a valid token is presented.

    Behavior matrix:
      * Cloud Run / prod, token unset                 → 404 (endpoint hidden)
      * Cloud Run / prod, token set, bad/missing hdr  → 404 (looks identical
                                                            to a non-existent
                                                            route — no probing)
      * Cloud Run / prod, token set, valid header     → 200
      * Dev / Replit (no K_SERVICE), token unset      → 200 (open, with warn)
      * Dev / Replit, token set                       → same token rules as prod
    """
    expected = os.environ.get("DEBUG_COLLECT_TOKEN", "").strip() or None
    on_cloud_run = bool(os.environ.get("K_SERVICE"))

    if expected is None:
        if on_cloud_run:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
        logger.warning(
            "[debug-collect] DEBUG_COLLECT_TOKEN unset; allowing in non-Cloud-Run env"
        )
        return

    presented = None
    if authorization and authorization.startswith(_BEARER_PREFIX):
        presented = authorization[len(_BEARER_PREFIX):].strip() or None
    if presented is None and x_debug_token:
        presented = x_debug_token.strip() or None

    if presented is None or not hmac.compare_digest(presented, expected):
        # Intentionally 404 (not 401/403) so unauthenticated callers cannot
        # distinguish "route exists but you lack auth" from "route does not
        # exist". Prevents endpoint discovery via response-code probing.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)


# ─── In-memory log capture ──────────────────────────────────────────────────


class _CollectLogCapture(logging.Handler):
    """Capture INFO/WARNING/ERROR records from
    ``app.tasks.collect_market_data`` for the duration of the request,
    so we can surface per-symbol counts and errors in the JSON response
    even when the operator can't tail logs (mobile UI, automation).
    The handler is removed in a finally block so a leaked handler
    cannot pollute later requests."""

    def __init__(self) -> None:
        super().__init__(level=logging.INFO)
        self.records: List[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.records.append(record.getMessage())
        except Exception:
            # Never let a logging handler raise into the application.
            pass


def _parse_collector_log(records: List[str]) -> Dict[str, Any]:
    fetched: Dict[str, int] = {}
    ok_symbols: List[str] = []
    errors: List[Dict[str, str]] = []
    empty: List[Dict[str, str]] = []

    for msg in records:
        m = _RX_FETCHED.search(msg)
        if m:
            try:
                fetched[m.group("sym")] = int(m.group("rows"))
            except ValueError:
                # rows="None" lands here — treat as 0 fetched.
                fetched[m.group("sym")] = 0
            continue
        m = _RX_OK.search(msg)
        if m:
            ok_symbols.append(m.group("sym"))
            continue
        m = _RX_FAILED.search(msg)
        if m:
            err_text = m.group("err")
            # Trim the asyncpg/SQLAlchemy verbiage to the first line so
            # the JSON payload stays compact.
            errors.append({
                "symbol": m.group("sym"),
                "error": err_text.splitlines()[0][:240],
            })
            continue
        m = _RX_EMPTY.search(msg)
        if m:
            empty.append({"symbol": m.group("sym"), "reason": m.group("reason")})

    return {
        "symbols_fetched": fetched,
        "symbols_persisted_ok": ok_symbols,
        "errors": errors,
        "empty_responses": empty,
    }


# ─── DB pre/post snapshot ───────────────────────────────────────────────────


async def _ohlcv_snapshot(window_minutes: int = 5) -> Dict[str, Any]:
    """Read-only snapshot of recent ``ohlcv`` activity. Never raises —
    a degraded snapshot must not mask a successful collect. Tolerates
    the table being absent (returns ``ok=False`` with the error)."""
    from ..database import AsyncSessionLocal
    try:
        async with AsyncSessionLocal() as db:
            row = (await db.execute(text(f"""
                SELECT COUNT(*) AS rows,
                       MAX(time) AS most_recent
                FROM ohlcv
                WHERE time > NOW() - INTERVAL '{int(window_minutes)} minutes'
            """))).first()
            most_recent = row.most_recent if row else None
            return {
                "ok": True,
                "rows_in_window": int(row.rows or 0) if row else 0,
                "window_minutes": window_minutes,
                "most_recent_ohlcv_time": (
                    most_recent.isoformat() if most_recent is not None else None
                ),
            }
    except Exception as exc:
        return {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "window_minutes": window_minutes,
        }


# ─── Endpoint ───────────────────────────────────────────────────────────────


@router.get("/run-collect")
async def run_collect(
    pipeline: str = Query(
        "5m",
        pattern="^(5m|all)$",
        description="Which collector to invoke: ``5m`` (microstructure, "
                    "default) or ``all`` (structural 1h sweep + ticker "
                    "metadata).",
    ),
    market: Optional[str] = Query(
        None,
        description="Advisory only — the underlying collector reads "
                    "``pool_coins`` for the full approved universe and "
                    "ignores per-call market filtering. Echoed back in "
                    "the response so the operator can confirm intent.",
    ),
    limit: Optional[int] = Query(
        None, ge=1,
        description="Advisory only — see ``market``. Echoed back.",
    ),
    authorization: Optional[str] = Header(None, alias="Authorization"),
    x_debug_token: Optional[str] = Header(None, alias="X-Debug-Token"),
) -> Dict[str, Any]:
    """Run the existing OHLCV collector synchronously and return a
    structured diagnostic summary. Reuses
    ``app.tasks.collect_market_data._collect_5m_async`` (or
    ``_collect_all_async`` when ``?pipeline=all``) — no business logic
    is duplicated here."""
    _enforce_auth(authorization, x_debug_token)

    started_at = datetime.now(timezone.utc)
    t0 = time.perf_counter()

    # 1. Pre-snapshot: how many ohlcv rows already in the recent window?
    pre_snapshot = await _ohlcv_snapshot(window_minutes=5)

    # 2. Attach the in-memory log capture to the collector's logger so
    #    the per-symbol records emitted during the call land in our
    #    response payload. Always remove in finally so handlers do not
    #    leak across requests.
    capture = _CollectLogCapture()
    collector_logger = logging.getLogger("app.tasks.collect_market_data")
    collector_logger.addHandler(capture)

    logger.info(
        "DEBUG COLLECT START pipeline=%s market=%s limit=%s pre_window_rows=%s "
        "pre_window_most_recent=%s",
        pipeline, market, limit,
        pre_snapshot.get("rows_in_window"),
        pre_snapshot.get("most_recent_ohlcv_time"),
    )

    collector_status = "ok"
    collector_return: Optional[str] = None
    collector_error: Optional[Dict[str, str]] = None

    try:
        if pipeline == "all":
            from ..tasks.collect_market_data import _collect_all_async
            count = await _collect_all_async()
            collector_return = f"Collected {count} symbols (1h structural sweep)"
        else:
            from ..tasks.collect_market_data import _collect_5m_async
            count = await _collect_5m_async()
            collector_return = f"Collected 5m data for {count} symbols"
    except Exception as exc:
        collector_status = "failed"
        collector_error = {
            "type": type(exc).__name__,
            "message": str(exc)[:500],
        }
        logger.error(
            "[DEBUG COLLECT] collector raised %s: %s",
            type(exc).__name__, exc,
            exc_info=True,
        )
    finally:
        collector_logger.removeHandler(capture)

    duration_ms = round((time.perf_counter() - t0) * 1000.0, 1)

    # 3. Post-snapshot: did rows actually land?
    post_snapshot = await _ohlcv_snapshot(window_minutes=5)

    parsed = _parse_collector_log(capture.records)
    symbols_attempted = sorted(set(parsed["symbols_fetched"].keys())
                               | {e["symbol"] for e in parsed["errors"]}
                               | {e["symbol"] for e in parsed["empty_responses"]}
                               | set(parsed["symbols_persisted_ok"]))
    symbols_persisted = sorted(set(parsed["symbols_persisted_ok"]))

    inserted_delta = (
        (post_snapshot.get("rows_in_window") or 0)
        - (pre_snapshot.get("rows_in_window") or 0)
        if pre_snapshot.get("ok") and post_snapshot.get("ok")
        else None
    )

    # 4. Diagnose CASE A vs CASE B per the operator playbook.
    diagnosis = _diagnose(
        collector_status=collector_status,
        symbols_attempted=symbols_attempted,
        symbols_persisted=symbols_persisted,
        inserted_delta=inserted_delta,
        post_snapshot=post_snapshot,
    )

    logger.info(
        "DEBUG COLLECT END duration_ms=%.1f attempted=%d persisted_ok=%d "
        "errors=%d post_window_rows=%s diagnosis=%s",
        duration_ms,
        len(symbols_attempted),
        len(symbols_persisted),
        len(parsed["errors"]),
        post_snapshot.get("rows_in_window"),
        diagnosis["case"],
    )

    return {
        "status": collector_status,
        "pipeline": pipeline,
        "market": market,
        "limit": limit,
        "started_at": started_at.isoformat(),
        "duration_ms": duration_ms,
        "approved_universe_count": len(symbols_attempted),
        "symbols_attempted": symbols_attempted,
        "symbols_persisted": symbols_persisted,
        "symbols_fetched_rows": parsed["symbols_fetched"],
        "errors": parsed["errors"],
        "empty_responses": parsed["empty_responses"],
        "ohlcv_window_before": pre_snapshot,
        "ohlcv_window_after": post_snapshot,
        "ohlcv_inserted_delta": inserted_delta,
        "most_recent_ohlcv_time": post_snapshot.get("most_recent_ohlcv_time"),
        "collector_return": collector_return,
        "collector_error": collector_error,
        "diagnosis": diagnosis,
        "log_record_count": len(capture.records),
    }


def _diagnose(
    *,
    collector_status: str,
    symbols_attempted: List[str],
    symbols_persisted: List[str],
    inserted_delta: Optional[int],
    post_snapshot: Dict[str, Any],
) -> Dict[str, str]:
    """Map (collector outcome, persistence delta) → CASE A / B / PARTIAL.

    Truth table — read top-to-bottom, first match wins:

      collector raised    + delta unknown         → CASE_B (persistence path broken)
      attempted == 0                               → CASE_B (universe empty, e.g. is_approved drift)
      collector ok + delta > 0 + errors == 0       → CASE_A (collector healthy, suspect Celery)
      collector ok + delta > 0 + errors > 0        → CASE_PARTIAL (some symbols failing)
      collector ok + delta == 0                    → CASE_B (no rows landed despite no exception)
      everything else                              → CASE_UNKNOWN
    """
    errors_present = len(symbols_attempted) > len(symbols_persisted)

    if collector_status == "failed":
        return {
            "case": "CASE_B",
            "summary": "Coletor levantou exceção — pipeline de persistência quebrado.",
            "next_action": "Inspecionar logs [DEBUG COLLECT] no workflow Backend API e o campo collector_error desta resposta. Comum: drift de is_approved em pool_coins (ver Task #220), credenciais Gate.io ausentes, ou tabela ohlcv inexistente.",
        }
    if not symbols_attempted:
        return {
            "case": "CASE_B",
            "summary": "Universo aprovado vazio — get_approved_pool_symbols_with_market_type retornou 0 símbolos.",
            "next_action": "Verificar pool_coins.is_approved (drift coberto pela Task #220) ou marcar manualmente alguns símbolos como aprovados para destravar a coleta em dev.",
        }
    if inserted_delta is not None and inserted_delta > 0 and not errors_present:
        return {
            "case": "CASE_A",
            "summary": f"Coletor saudável — {inserted_delta} novas linhas em ohlcv. O problema é Celery worker/beat NÃO consumindo a fila microstructure.",
            "next_action": "Subir os workflows Celery Worker (--queues=microstructure,structural,execution) e Celery Beat (Task #220 passo 3-4). pipeline_scan já está em TASK_ROUTES + beat_schedule.",
        }
    if inserted_delta is not None and inserted_delta > 0 and errors_present:
        return {
            "case": "CASE_PARTIAL",
            "summary": f"Coletor parcial — {inserted_delta} linhas inseridas, mas {len(symbols_attempted) - len(symbols_persisted)} símbolo(s) falharam.",
            "next_action": "Revisar campo errors[] desta resposta. Falhas residuais não bloqueiam a pipeline; subir Celery normalmente.",
        }
    if inserted_delta == 0:
        return {
            "case": "CASE_B",
            "summary": "Coletor terminou sem exceção mas nenhuma linha nova em ohlcv — falha silenciosa na persistência ou todos os símbolos retornaram empty.",
            "next_action": "Inspecionar empty_responses[] e errors[]. Comum: símbolos do pool inexistentes na exchange, formato BTCUSDT vs BTC_USDT, ou rate-limit da Gate.",
        }
    return {
        "case": "CASE_UNKNOWN",
        "summary": "Estado inconclusivo — snapshot pre/post indisponível ou sinais mistos.",
        "next_action": "Revisar ohlcv_window_before / ohlcv_window_after / log_record_count desta resposta.",
    }
