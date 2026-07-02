"""Bounded, audited execution of Co-Pilot read-only SQL."""

from datetime import date, datetime
from decimal import Decimal
import json
import os
from time import perf_counter
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import AsyncSessionLocal
from ..models.copilot import CopilotAuditLog, CopilotQueryRun
from .sql_guard import SqlGuardError, classify_sql


def _json_value(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (UUID, Decimal)):
        return str(value)
    if isinstance(value, bytes):
        return f"<bytes:{len(value)}>"
    return value


class QueryExecutor:
    def __init__(self, session_factory=AsyncSessionLocal):
        self._session_factory = session_factory
        self.timeout_seconds = max(1, min(int(os.getenv("COPILOT_QUERY_TIMEOUT_SECONDS", "30")), 120))
        self.max_rows = max(1, min(int(os.getenv("COPILOT_QUERY_MAX_ROWS", "1000")), 10_000))
        self.max_result_bytes = max(1024, min(int(os.getenv("COPILOT_QUERY_MAX_RESULT_BYTES", "1048576")), 10_485_760))

    async def execute(
        self,
        audit_db: AsyncSession,
        user_id: UUID,
        sql: str,
        params: dict[str, Any] | None = None,
        *,
        reason: str,
        session_id: UUID | None = None,
    ) -> dict[str, Any]:
        params = params or {}
        guard = classify_sql(sql)
        started = perf_counter()
        rows: list[dict[str, Any]] = []
        columns: list[str] = []
        truncated = False
        error: str | None = None
        try:
            first = guard.normalized_sql.lstrip().split(None, 1)[0].lower()
            statement = guard.normalized_sql
            if first in {"select", "with"}:
                statement = (
                    "SELECT * FROM (" + guard.normalized_sql + ") AS _copilot_bounded "
                    f"LIMIT {self.max_rows + 1}"
                )
            async with self._session_factory() as read_db:
                async with read_db.begin():
                    await read_db.execute(text(f"SET LOCAL statement_timeout = {self.timeout_seconds * 1000}"))
                    await read_db.execute(text("SET TRANSACTION READ ONLY"))
                    result = await read_db.execute(text(statement), params)
                    columns = list(result.keys())
                    raw_rows = result.fetchmany(self.max_rows + 1)
            truncated = len(raw_rows) > self.max_rows
            for raw in raw_rows[: self.max_rows]:
                item = {columns[i]: _json_value(value) for i, value in enumerate(raw)}
                candidate_size = len(json.dumps(rows + [item], ensure_ascii=False, default=str).encode("utf-8"))
                if candidate_size > self.max_result_bytes:
                    truncated = True
                    break
                rows.append(item)
            status = "COMPLETED"
        except Exception as exc:
            status = "FAILED"
            error = f"{type(exc).__name__}: {str(exc)[:1000]}"

        elapsed_ms = int((perf_counter() - started) * 1000)
        run = CopilotQueryRun(
            user_id=user_id, session_id=session_id, query_text=guard.normalized_sql,
            query_hash=guard.query_hash, query_type=guard.classification, reason=reason,
            parameters=params, status=status, rows_returned=len(rows), execution_ms=elapsed_ms,
            result_preview=rows[:20], result_truncated=truncated, error=error,
        )
        audit_db.add(run)
        audit_db.add(CopilotAuditLog(
            user_id=user_id, session_id=session_id,
            event_type="QUERY_EXECUTED" if not error else "QUERY_FAILED",
            actor_user_id=user_id,
            payload={"query_hash": guard.query_hash, "classification": guard.classification,
                     "reason": reason, "rows_returned": len(rows), "execution_ms": elapsed_ms,
                     "truncated": truncated, "error": error},
        ))
        await audit_db.commit()
        await audit_db.refresh(run)
        if error:
            raise RuntimeError(error)
        return {
            "id": str(run.id), "classification": guard.classification,
            "query": guard.normalized_sql, "query_hash": guard.query_hash,
            "columns": columns, "rows": rows, "rows_returned": len(rows),
            "execution_ms": elapsed_ms, "truncated": truncated,
        }


__all__ = ["QueryExecutor", "SqlGuardError"]
