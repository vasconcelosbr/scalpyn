"""Fail-fast database schema gate executed before the API starts."""

from __future__ import annotations

import asyncio
import os
import sys

from app._critical_schema import CRITICAL_COLUMNS


def _to_asyncpg_url(url: str) -> str:
    if url.startswith("postgresql+asyncpg://"):
        return "postgresql://" + url[len("postgresql+asyncpg://") :]
    if url.startswith("postgres://"):
        return "postgresql://" + url[len("postgres://") :]
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
            "FATAL: cannot connect to database for schema check: "
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
            "FATAL: critical schema drift - "
            f"{len(missing)} of {len(CRITICAL_COLUMNS)} columns missing:",
            file=sys.stderr,
        )
        for table, column in missing:
            print(f"  - {table}.{column}", file=sys.stderr)
        print(
            "Container will not start. Apply the missing DDL manually "
            "(see docs/runbooks/critical-schema-drift.md) or fix the "
            "alembic upgrade lock contention so the migrations can run.",
            file=sys.stderr,
        )
        return 1
    print(f"OK: all {len(CRITICAL_COLUMNS)} critical columns present.", file=sys.stderr)
    return 0


def main() -> int:
    try:
        return asyncio.run(_check())
    except KeyboardInterrupt:
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
