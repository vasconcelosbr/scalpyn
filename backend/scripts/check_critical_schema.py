"""Post-migration schema gate — fail fast when critical columns are missing.

Used by ``start.sh`` after the ``alembic stamp head`` fallback so the silent
schema-drift mode (recorded in alembic_version but DDL never applied) cannot
proceed to the uvicorn boot.  Without this gate, the application starts up
"healthy" but ~30k UndefinedColumnError exceptions per day flood Sentry and
the cascading failed transactions exhaust the connection pool (Task #178).

Exit codes
----------
0 — all critical (table, column) pairs are present.
1 — at least one critical pair is missing; container should not start.
2 — could not connect to the database; treated as fatal.

Usage from start.sh::

    python3 -m scripts.check_critical_schema || exit 1

The list itself lives in ``app/_critical_schema.py`` — single source of
truth, imported by both this script and ``app.main.health_check_schema``.
That module is intentionally a zero-dependency leaf (no SQLAlchemy, no
FastAPI, no app config), so importing it from a stripped-down boot context
is safe and fast.
"""

from __future__ import annotations

import asyncio
import os
import sys

from app._critical_schema import CRITICAL_COLUMNS


def _to_asyncpg_url(url: str) -> str:
    """Normalize SQLAlchemy URL to one asyncpg.connect accepts."""
    if url.startswith("postgresql+asyncpg://"):
        return "postgresql://" + url[len("postgresql+asyncpg://"):]
    if url.startswith("postgres://"):
        return "postgresql://" + url[len("postgres://"):]
    return url


async def _check() -> int:
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("FATAL: DATABASE_URL is not set; cannot probe schema.", file=sys.stderr)
        return 2

    try:
        import asyncpg  # type: ignore[import-not-found]
    except ImportError as exc:
        print(f"FATAL: asyncpg not importable: {exc}", file=sys.stderr)
        return 2

    try:
        conn = await asyncpg.connect(_to_asyncpg_url(url), timeout=10)
    except Exception as exc:
        print(
            f"FATAL: cannot connect to database for schema check: "
            f"{type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 2

    try:
        rows = await conn.fetch(
            """
            SELECT table_name, column_name
            FROM information_schema.columns
            WHERE table_schema = 'public'
            """
        )
    finally:
        try:
            await conn.close()
        except Exception:
            pass

    present = {(r["table_name"], r["column_name"]) for r in rows}
    missing = [(t, c) for (t, c) in CRITICAL_COLUMNS if (t, c) not in present]

    if missing:
        print(
            "FATAL: critical schema drift — "
            f"{len(missing)} of {len(CRITICAL_COLUMNS)} columns missing:",
            file=sys.stderr,
        )
        for table, column in missing:
            print(f"  - {table}.{column}", file=sys.stderr)
        print(
            "Container will not start. Apply the missing DDL manually "
            "(see docs/runbooks/scheduler-group-drift.md) or fix the "
            "alembic upgrade lock contention so the migrations can run.",
            file=sys.stderr,
        )
        return 1

    print(
        f"OK: all {len(CRITICAL_COLUMNS)} critical columns present.",
        file=sys.stderr,
    )
    return 0


def main() -> int:
    try:
        return asyncio.run(_check())
    except KeyboardInterrupt:
        return 2


if __name__ == "__main__":
    sys.exit(main())
