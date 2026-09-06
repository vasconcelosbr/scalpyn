"""Static contract checks for revision 220.

The production failure was a mismatch between the value emitted by the
continuation worker and the database allowlist. These checks do not require a
database and keep that producer/constraint relationship explicit.
"""

import importlib.util
from pathlib import Path


def _load_migration():
    path = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "220_shadow_l3_closure_path.py"
    )
    spec = importlib.util.spec_from_file_location("shadow_l3_closure_path", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_extends_existing_allowlist_idempotently():
    module = _load_migration()
    statements = []

    class FakeOp:
        @staticmethod
        def execute(statement):
            statements.append(str(statement))

    module.op = FakeOp()
    module.upgrade()

    sql = "\n".join(statements)
    assert module.revision == "220_shadow_l3_closure_path"
    assert module.down_revision == "219_shadow_l3_continuation"
    assert "pg_get_constraintdef(oid) LIKE '%l3_continuation%'" in sql
    assert "DROP CONSTRAINT IF EXISTS ck_shadow_trades_closure_path" in sql
    assert "'fast_scan'" in sql
    assert "'regular_batch'" in sql
    assert "'canonical_walk'" in sql
    assert "'l3_continuation'" in sql


def test_continuation_worker_uses_the_migrated_value():
    service = (
        Path(__file__).parents[1]
        / "app"
        / "services"
        / "shadow_l3_exit_service.py"
    ).read_text(encoding="utf-8")

    assert 'closure_path="l3_continuation"' in service
