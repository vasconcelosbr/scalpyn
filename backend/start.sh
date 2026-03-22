#!/bin/sh
# Scalpyn backend startup script
# Runs Alembic migrations before starting the application server.
#
# Safety mechanism for DBs bootstrapped by create_all (no alembic_version):
#   If the pools table already exists but alembic_version is empty/missing,
#   we stamp the DB at the base revision so Alembic knows where to start,
#   then run 'upgrade head' to apply only the newer migrations.

set -e

echo "==> Checking database migration state..."

# Stamp the DB at the initial revision if alembic_version table is missing
# or empty (i.e., the schema was created by create_all without Alembic history).
# This prevents migration 001 from trying to re-add 'overrides' and failing
# on a DB where create_all already created it.
python - <<'PYEOF'
import os
import sys
try:
    from sqlalchemy import create_engine, text, inspect
    from alembic.config import Config
    from alembic import command

    db_url = os.environ.get("DATABASE_URL", "")
    if not db_url:
        print("  DATABASE_URL not set — skipping stamp check")
        sys.exit(0)

    # Use synchronous engine for this quick check
    sync_url = db_url.replace("postgresql+asyncpg://", "postgresql://")
    engine = create_engine(sync_url, connect_args={"connect_timeout": 10})

    with engine.connect() as conn:
        insp = inspect(conn)

        # Does alembic_version table exist?
        has_alembic = insp.has_table("alembic_version")

        if has_alembic:
            result = conn.execute(text("SELECT version_num FROM alembic_version"))
            versions = [r[0] for r in result]
        else:
            versions = []

        # Does the pools table already exist (bootstrapped by create_all)?
        has_pools = insp.has_table("pools")

    engine.dispose()

    if has_pools and not versions:
        print("  DB was bootstrapped by create_all — stamping alembic to base revision...")
        cfg = Config("/app/alembic.ini")
        # Stamp at None (base) so alembic knows to run from the very first migration
        # All migrations use IF NOT EXISTS so they are safe to run on existing schema
        command.stamp(cfg, "base")
        print("  Stamped at base.")
    elif versions:
        print(f"  Current alembic revision(s): {versions}")
    else:
        print("  Fresh database — running all migrations from scratch.")

except Exception as e:
    print(f"  Warning: stamp check failed ({e}) — proceeding with upgrade anyway")
PYEOF

echo "==> Running: alembic upgrade head"
alembic upgrade head
echo "==> Migrations complete."

echo "==> Starting uvicorn..."
exec uvicorn app.main:app \
    --host 0.0.0.0 \
    --port "${PORT:-8080}" \
    --workers "${WEB_CONCURRENCY:-2}"
