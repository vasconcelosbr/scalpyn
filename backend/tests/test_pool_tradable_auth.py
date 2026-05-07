"""Authorization contract for ``POST /api/pools/{id}/coins/{symbol}/tradable``.

Task #232 — the execution-gate toggle MUST honour:

* owner          → 200 (Pool.user_id == caller).
* admin / superuser → 200 (any pool, ownership bypassed).
* non-owner role=trader → 404 (pool hidden, no info leak).
* trying to flip ``tradable=true`` on an inactive symbol → 400.

Uses FastAPI dependency overrides + an in-memory ``AsyncSession`` stub
so the test does not touch the real Postgres pool.
"""

from __future__ import annotations

import os
import sys
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from types import SimpleNamespace

from app.api import pools as pools_api  # noqa: E402
from app.database import get_db  # noqa: E402


# ── Stub session ────────────────────────────────────────────────────────────


class _Result:
    def __init__(self, obj):
        self._obj = obj

    def scalars(self):
        return self

    def first(self):
        return self._obj


class _StubSession:
    """Mimics just enough of AsyncSession for the tradable endpoint."""

    def __init__(self, user, pool, coin):
        self.user = user
        self.pool = pool
        self.coin = coin
        self.commits = 0

    async def execute(self, stmt):
        # Inspect the FROM target on the compiled statement.
        target = None
        try:
            froms = list(stmt.get_final_froms())  # type: ignore[attr-defined]
            target = froms[0].name if froms else None
        except Exception:
            target = str(stmt).lower()
        if "users" in (target or ""):
            return _Result(self.user)
        if "pool_coins" in (target or ""):
            return _Result(self.coin)
        if "pools" in (target or ""):
            return _Result(self.pool)
        return _Result(None)

    async def commit(self):
        self.commits += 1

    async def refresh(self, _obj):
        return None


def _build_app(session: _StubSession, caller_id) -> TestClient:
    app = FastAPI()
    app.include_router(pools_api.router)

    async def _override_db():
        yield session

    async def _override_user_id():
        return caller_id

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[pools_api.get_current_user_id] = _override_user_id
    return TestClient(app, raise_server_exceptions=True)


def _mk_user(role: str):
    return SimpleNamespace(id=uuid4(), role=role)


def _mk_pool(owner_id):
    return SimpleNamespace(id=uuid4(), user_id=owner_id)


def _mk_coin(pool_id, *, is_active: bool = True, is_tradable: bool = False):
    return SimpleNamespace(
        id=uuid4(),
        pool_id=pool_id,
        symbol="BTC_USDT",
        market_type="spot",
        is_active=is_active,
        is_tradable=is_tradable,
        is_approved=is_active,
        added_at=None,
        origin="manual",
        discovered_at=None,
    )


# ── Tests ───────────────────────────────────────────────────────────────────


def test_owner_can_toggle_tradable() -> None:
    owner = _mk_user("trader")
    pool = _mk_pool(owner.id)
    coin = _mk_coin(pool.id, is_active=True, is_tradable=False)
    session = _StubSession(owner, pool, coin)
    client = _build_app(session, owner.id)

    r = client.post(
        f"/api/pools/{pool.id}/coins/BTC_USDT/tradable",
        json={"tradable": True},
    )
    assert r.status_code == 200, r.text
    assert coin.is_tradable is True
    assert session.commits == 1


def test_admin_bypasses_ownership() -> None:
    admin = _mk_user("admin")
    other_owner_id = uuid4()
    pool = _mk_pool(other_owner_id)
    coin = _mk_coin(pool.id, is_active=True, is_tradable=False)
    session = _StubSession(admin, pool, coin)
    client = _build_app(session, admin.id)

    r = client.post(
        f"/api/pools/{pool.id}/coins/BTC_USDT/tradable",
        json={"tradable": True},
    )
    assert r.status_code == 200, r.text
    assert coin.is_tradable is True


def test_non_owner_trader_gets_404() -> None:
    real_owner_id = uuid4()
    intruder = _mk_user("trader")
    pool = _mk_pool(real_owner_id)
    coin = _mk_coin(pool.id)
    # The endpoint scopes Pool by user_id when caller is not admin;
    # our stub returns the pool only when the SELECT hits ``pools``.
    # To simulate "no row found for this caller", drop the pool.
    session = _StubSession(intruder, None, coin)
    client = _build_app(session, intruder.id)

    r = client.post(
        f"/api/pools/{pool.id}/coins/BTC_USDT/tradable",
        json={"tradable": True},
    )
    assert r.status_code == 404
    assert session.commits == 0


def test_cannot_enable_tradable_on_inactive_symbol() -> None:
    owner = _mk_user("trader")
    pool = _mk_pool(owner.id)
    coin = _mk_coin(pool.id, is_active=False, is_tradable=False)
    session = _StubSession(owner, pool, coin)
    client = _build_app(session, owner.id)

    r = client.post(
        f"/api/pools/{pool.id}/coins/BTC_USDT/tradable",
        json={"tradable": True},
    )
    assert r.status_code == 400
    assert coin.is_tradable is False
    assert session.commits == 0


def test_legacy_is_tradable_body_key_still_accepted() -> None:
    """One-deploy backwards-compat: clients may still send ``is_tradable``."""
    owner = _mk_user("trader")
    pool = _mk_pool(owner.id)
    coin = _mk_coin(pool.id, is_active=True, is_tradable=False)
    session = _StubSession(owner, pool, coin)
    client = _build_app(session, owner.id)

    r = client.post(
        f"/api/pools/{pool.id}/coins/BTC_USDT/tradable",
        json={"is_tradable": True},
    )
    assert r.status_code == 200
    assert coin.is_tradable is True
