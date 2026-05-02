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
  * **End-to-end failover**: a candidate ``_GateWSSupervisor`` actually
    starts the WS once the previous leader's lock expires, with no
    process restart.
"""

from __future__ import annotations

import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import fakeredis.aioredis  # type: ignore[import-untyped]

from app.services import gate_ws_leader as leader_mod
from app.services.gate_ws_leader import (
    LEADER_KEY,
    LEADER_TTL_SECONDS,
    _GateWSSupervisor,
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


# ── End-to-end failover (supervisor) ──────────────────────────────────────────

class _FakeGateWSClient:
    """Stand-in for ``GateWSClient`` so the supervisor doesn't open real sockets."""


@pytest.mark.anyio
async def test_supervisor_takes_over_after_leader_lock_expires(monkeypatch, fake_redis):
    """A reader replica becomes leader when the previous leader's lock expires.

    This is the regression that the first code review caught: the old
    ``start_gate_ws_with_leader_election`` only tried to acquire once at
    startup, so a replica that lost the initial race stayed idle forever
    even after the leader died.  The supervisor must keep polling.
    """
    # Speed up the loops so this test stays well under a second.
    monkeypatch.setattr(leader_mod, "CANDIDATE_POLL_INTERVAL_SECONDS", 0.05)
    monkeypatch.setattr(leader_mod, "LEADER_RENEW_INTERVAL_SECONDS", 0.05)

    started_with_instance: list[str] = []
    stop_calls: list[None] = []

    async def fake_start_gate_ws(*, api_key, api_secret, contracts, spot_pairs, instance_id):
        started_with_instance.append(instance_id)
        return _FakeGateWSClient()

    async def fake_stop_gate_ws():
        stop_calls.append(None)

    def fake_register_all_handlers(_client):  # noqa: ANN001
        return None

    async def fake_resolve_spot_pairs():
        return ["BTC_USDT"]

    async def fake_load_credentials():
        return ("", "")

    # The supervisor lazily imports these — patch on the source modules.
    import app.websocket.gate_ws_client as ws_mod
    import app.websocket.event_handlers as eh_mod
    monkeypatch.setattr(ws_mod, "start_gate_ws", fake_start_gate_ws, raising=False)
    monkeypatch.setattr(ws_mod, "stop_gate_ws",  fake_stop_gate_ws,  raising=False)
    monkeypatch.setattr(eh_mod, "register_all_handlers", fake_register_all_handlers, raising=False)
    monkeypatch.setattr(leader_mod, "_resolve_spot_pairs",   fake_resolve_spot_pairs)
    monkeypatch.setattr(leader_mod, "_load_gate_credentials", fake_load_credentials)

    # Pre-populate the lock as if instance-A is the current leader.
    # Use a long TTL so we can decide *when* to release it; the test
    # then deletes it explicitly (simulating either a clean shutdown
    # or a TTL expiry — equivalent from the supervisor's POV).
    assert await _try_acquire_leader(fake_redis, "instance-A") is True

    supervisor = _GateWSSupervisor(fake_redis, "instance-B")
    supervisor.start()
    try:
        # Phase 1 — B is in candidate mode, has NOT started the WS.
        # Give the supervisor a couple of poll cycles to confirm.
        for _ in range(5):
            await asyncio.sleep(0.05)
            if started_with_instance:
                break
        assert started_with_instance == [], (
            "Supervisor should not have acquired leadership while A holds the lock"
        )
        assert supervisor.is_leader is False
        assert await fake_redis.get(LEADER_KEY) == b"instance-A"

        # Phase 2 — A's lock is gone (crash / clean shutdown / TTL expiry).
        await fake_redis.delete(LEADER_KEY)

        # Within ~CANDIDATE_POLL_INTERVAL_SECONDS, B should win the lock,
        # call start_gate_ws, and flip its is_leader flag.
        deadline = asyncio.get_event_loop().time() + 2.0
        while asyncio.get_event_loop().time() < deadline:
            if started_with_instance and supervisor.is_leader:
                break
            await asyncio.sleep(0.02)

        assert started_with_instance == ["instance-B"], (
            f"Expected instance-B to acquire leadership and start the WS exactly once; "
            f"got {started_with_instance}"
        )
        assert supervisor.is_leader is True
        assert await fake_redis.get(LEADER_KEY) == b"instance-B"
    finally:
        await supervisor.stop()

    # Phase 3 — clean shutdown stops the WS and releases the lock.
    assert stop_calls, "stop_gate_ws should have been called on shutdown"
    assert await fake_redis.get(LEADER_KEY) is None
