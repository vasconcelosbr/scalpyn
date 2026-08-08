"""Run Alembic through a Railway TCP proxy without printing credentials."""

from __future__ import annotations

import os
import sys
from urllib.parse import quote


def _public_database_url() -> str:
    public_url = os.environ.get("DATABASE_PUBLIC_URL")
    if public_url:
        return public_url
    host = os.environ.get("RAILWAY_TCP_PROXY_DOMAIN")
    port = os.environ.get("RAILWAY_TCP_PROXY_PORT")
    if not host or not port:
        raise RuntimeError("Railway public database URL or TCP proxy is required")
    user = quote(os.environ["PGUSER"], safe="")
    password = quote(os.environ["PGPASSWORD"], safe="")
    database = quote(os.environ["PGDATABASE"], safe="")
    return f"postgresql://{user}:{password}@{host}:{port}/{database}"


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("Usage: run_alembic_with_railway_proxy.py <alembic args>")
    os.environ["DATABASE_URL"] = _public_database_url()
    from alembic.config import main as alembic_main

    alembic_main(argv=sys.argv[1:])


if __name__ == "__main__":
    main()
