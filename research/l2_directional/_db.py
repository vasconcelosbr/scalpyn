"""Shared DB connection helper for L2 research scripts."""
from __future__ import annotations

import os
import asyncpg


async def connect() -> asyncpg.Connection:
    """Return a raw asyncpg connection using DATABASE_URL env var."""
    url = os.environ.get("DATABASE_URL") or os.environ.get("DB_URL")
    if not url:
        raise RuntimeError("DATABASE_URL env var not set. Run via: railway run python -m research.l2_directional.phase_XX")
    return await asyncpg.connect(url)
