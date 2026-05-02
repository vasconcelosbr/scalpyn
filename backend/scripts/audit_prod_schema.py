"""Pre-push schema drift auditor — cross-check alembic_version vs information_schema.

Run this LOCALLY (with DATABASE_URL temporarily pointed at prod) BEFORE pushing
any commit that adds an entry to ``app._critical_schema.CRITICAL_COLUMNS`` or
that touches ``backend/alembic/versions/``.  Catches the failure mode that
sank migrations 032/033/034 in May 2026: alembic recorded the revision as
applied (``alembic_version.version_num``), but the DDL never ran on the live
DB (lock contention with the previous revision's Celery beat).  ``start.sh``'s
``validate_critical_schema`` then exit-1s on cold-start and Cloud Run rolls
back the deploy with the generic "container failed to start and listen on
PORT=8080" message.

The boot-time gate ``scripts.check_critical_schema`` is the FAILSAFE — it runs
on every cold start and is what produced the loud failure that exposed the
032 drift.  This auditor is the PROACTIVE check — run it before pushing so
you fix drift on your own time, not during a Cloud Build window.

Usage
-----
From ``backend/``::

    DATABASE_URL=postgresql://prod-user:...@/scalpyn?host=/cloudsql/... \\
        python3 -m scripts.audit_prod_schema

Or against the local dev DB to sanity-check the script itself::

    cd backend && python3 -m scripts.audit_prod_schema

Output
------
* ``alembic_version.version_num`` (the head alembic believes is applied)
* For every critical (table, column) pair: PRESENT / MISSING
* For every MISSING column: the alembic version file that introduced it
  (best-effort grep) so you can copy the idempotent DDL straight into Cloud
  SQL Studio.
* Exit 0 if every critical column is present; exit 1 otherwise (so this can
  be wired into a pre-push hook later).

This script intentionally does NOT mutate the database — it only SELECTs
from ``alembic_version`` and ``information_schema.columns``.  Safe to run
against prod from any developer machine with read credentials.
"""

from __future__ import annotations

import asyncio
import os
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from app._critical_schema import CRITICAL_COLUMNS


def _to_asyncpg_url(url: str) -> str:
    if url.startswith("postgresql+asyncpg://"):
        return "postgresql://" + url[len("postgresql+asyncpg://") :]
    if url.startswith("postgres://"):
        return "postgresql://" + url[len("postgres://") :]
    return url


def _migration_for_column(table: str, column: str) -> Optional[Path]:
    """Best-effort grep for the migration file that introduces (table, column).

    Looks for the literal substring ``column`` in any ``versions/*.py`` file
    that also contains ``ALTER TABLE`` and the table name.  This is a heuristic
    aimed at the common ``ADD COLUMN IF NOT EXISTS`` pattern; it returns the
    first match (lowest filename when sorted), which for this repo's
    chronological numbering is the original introducer.
    """
    versions_dir = Path(__file__).resolve().parent.parent / "alembic" / "versions"
    if not versions_dir.is_dir():
        return None
    candidates: List[Path] = []
    needle_col = re.compile(rf"\b{re.escape(column)}\b")
    needle_tbl = re.compile(rf"\b{re.escape(table)}\b")
    for path in sorted(versions_dir.glob("*.py")):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if needle_col.search(text) and needle_tbl.search(text) and "ALTER TABLE" in text:
            candidates.append(path)
    return candidates[0] if candidates else None


async def _audit() -> int:
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("FATAL: DATABASE_URL is not set.", file=sys.stderr)
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
            f"FATAL: cannot connect: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 2

    try:
        try:
            version_row = await conn.fetchrow("SELECT version_num FROM alembic_version")
            alembic_head = version_row["version_num"] if version_row else "<empty>"
        except Exception as exc:
            alembic_head = f"<unreadable: {type(exc).__name__}: {exc}>"

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
    missing: List[Tuple[str, str]] = [
        (t, c) for (t, c) in CRITICAL_COLUMNS if (t, c) not in present
    ]

    print(f"alembic_version.version_num : {alembic_head}")
    print(
        f"critical columns            : "
        f"{len(CRITICAL_COLUMNS) - len(missing)}/{len(CRITICAL_COLUMNS)} present"
    )
    print()

    if not missing:
        print("OK — no drift detected.  Safe to push.")
        return 0

    print(f"DRIFT — {len(missing)} critical column(s) missing in this database:")
    print()
    grouped: Dict[Optional[Path], List[Tuple[str, str]]] = {}
    for table, column in missing:
        origin = _migration_for_column(table, column)
        grouped.setdefault(origin, []).append((table, column))

    for origin, pairs in grouped.items():
        if origin is None:
            print("  (introducing migration not found — search manually)")
        else:
            try:
                rel = origin.relative_to(Path.cwd())
            except ValueError:
                rel = origin
            print(f"  introduced by {rel}:")
        for table, column in pairs:
            print(f"    - {table}.{column}")
    print()
    print(
        "Next steps:\n"
        "  1) Open each migration file shown above and copy its idempotent DDL\n"
        "     (`ALTER TABLE … ADD COLUMN IF NOT EXISTS …`) into Cloud SQL Studio.\n"
        "  2) Re-run this auditor — it should report OK.\n"
        "  3) Then push.  See docs/runbooks/critical-schema-drift.md for the\n"
        "     full procedure and post-fix verification checklist."
    )
    return 1


def main() -> int:
    try:
        return asyncio.run(_audit())
    except KeyboardInterrupt:
        return 2


if __name__ == "__main__":
    sys.exit(main())
