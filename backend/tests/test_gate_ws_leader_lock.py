"""Tests for the Redis-backed leader-election in ``services/gate_ws_leader``.

The lock is a tiny ``SET NX EX`` + Lua-script renew/release dance, but
it's the only thing that prevents N Cloud Run replicas from each opening
their own Gate.io WS and multiplying the taker-flow ingestion N-fold.
These tests pin the contract:

  * Two replicas race; exactly one wins ``_try_acquire_leader``.
  * The winner can extend its TTL via ``_renew_leader``; the loser cannot.
  * After the lock expires the loser can take over.
  * ``_release_leader`` is owner-checked so a stale call from a former
    leader cannot evict the current one.
"""

from __future__ import annotations

import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import fakeredis.aioredis  # type: ignore[import-untyped]

from app.services.gate_ws_leader import (
    LEADER_KEY,
    LEADER_TTL_SECONDS,
    _release_leader,
    _renew_leader,
    _try_acquire_leader,
)


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def fake_redis():
    return fakeredis.aioredis.FakeRedis(decode_responses=False)


@pytest.mark.anyio
async def test_only_one_instance_acquires_leader(fake_redis):
    a_won = await _try_acquire_leader(fake_redis, "instance-A")
    b_won = await _try_acquire_leader(fake_redis, "instance-B")
    assert a_won is True
    assert b_won is False

    stored = await fake_redis.get(LEADER_KEY)
    assert stored == b"instance-A"


@pytest.mark.anyio
async def test_renew_extends_ttl_only_for_owner(fake_redis):
    assert await _try_acquire_leader(fake_redis, "instance-A") is True

    # Reduce TTL to a small value to detect that the renewal actually moves it.
    await fake_redis.expire(LEADER_KEY, 5)
    ttl_before = await fake_redis.ttl(LEADER_KEY)
    assert ttl_before <= 5

    # Owner renews → TTL bumped to LEADER_TTL_SECONDS.
    assert await _renew_leader(fake_redis, "instance-A") is True
    ttl_after = await fake_redis.ttl(LEADER_KEY)
    assert ttl_after > ttl_before
    assert ttl_after <= LEADER_TTL_SECONDS

    # Non-owner renew is a no-op and signals "you lost the lock" via False.
    assert await _renew_leader(fake_redis, "instance-B") is False


@pytest.mark.anyio
async def test_release_only_evicts_when_caller_is_owner(fake_redis):
    assert await _try_acquire_leader(fake_redis, "instance-A") is True

    # A non-owner release must NOT delete the key — protects against a
    # stale ex-leader that sneaks back in after losing the lock.
    await _release_leader(fake_redis, "instance-B")
    assert await fake_redis.get(LEADER_KEY) == b"instance-A"

    # The owner releases → key gone, next acquire wins.
    await _release_leader(fake_redis, "instance-A")
    assert await fake_redis.get(LEADER_KEY) is None
    assert await _try_acquire_leader(fake_redis, "instance-B") is True


@pytest.mark.anyio
async def test_expired_lock_transfers_to_new_leader(fake_redis):
    """When the original leader's TTL expires, another replica can take over."""
    assert await _try_acquire_leader(fake_redis, "instance-A") is True
    # Force expiry by deleting the key — equivalent to TTL=0 from the
    # caller's perspective (fakeredis honors expirations but we don't
    # want to sleep for the real LEADER_TTL_SECONDS in a unit test).
    await fake_redis.delete(LEADER_KEY)

    # B can now claim leadership; A's renew correctly reports failure.
    assert await _try_acquire_leader(fake_redis, "instance-B") is True
    assert await _renew_leader(fake_redis, "instance-A") is False
    assert await fake_redis.get(LEADER_KEY) == b"instance-B"
