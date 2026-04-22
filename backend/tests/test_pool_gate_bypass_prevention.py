"""
Tests for pool gate bypass prevention in pipeline_scan.

When a POOL-level watchlist (source_pool_id + filter conditions) exists for a
given (user_id, pool_id), any downstream L1/L2/L3 watchlist that shares the
same source_pool_id must NOT read directly from pool_coins.  Instead it must
consume only the POOL-approved assets so that POOL-level rejections are
propagated correctly through the whole pipeline.

These tests verify the map-building and gate-lookup logic that drives the
symbol-universe selection inside _run_pipeline_scan.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.utils.pipeline_profile_filters import effective_pipeline_level


# ── helpers ───────────────────────────────────────────────────────────────────

def _build_pool_wl_id_map(wl_rows: list[dict]) -> dict[tuple[str, str], str]:
    """Reproduce the map-building logic from _run_pipeline_scan."""
    pool_wl_id_map: dict[tuple[str, str], str] = {}
    for wl in wl_rows:
        if not wl.get("source_pool_id"):
            continue
        eff = effective_pipeline_level(
            wl["level"],
            source_pool_id=wl["source_pool_id"],
            profile_config=wl.get("profile_config"),
        )
        if eff == "POOL":
            pool_wl_id_map[(str(wl["user_id"]), str(wl["source_pool_id"]))] = str(wl["id"])
    return pool_wl_id_map


def _resolve_gate_wl_id(
    wl: dict,
    pool_wl_id_map: dict[tuple[str, str], str],
) -> str | None:
    """Reproduce the gate-resolution logic from _run_pipeline_scan."""
    level = (wl.get("level") or "L1").upper()
    pre_eff = effective_pipeline_level(
        level,
        source_pool_id=wl.get("source_pool_id"),
        profile_config=wl.get("profile_config"),
    )
    if pre_eff == "POOL":
        return None  # This watchlist IS the POOL gatekeeper — no gate to apply
    return pool_wl_id_map.get((str(wl["user_id"]), str(wl["source_pool_id"])))


# ── fixtures ──────────────────────────────────────────────────────────────────

POOL_PROFILE_CONFIG = {
    "filters": {
        "conditions": [
            {"field": "market_cap", "operator": ">=", "value": 100_000_000},
        ]
    }
}

L1_PROFILE_CONFIG = {
    "filters": {
        "conditions": [
            {"field": "volume_24h", "operator": ">=", "value": 500_000},
        ]
    }
}

USER_ID = "user-1"
POOL_ID = "pool-gate"
POOL_WL_ID = "wl-pool"
L1_WL_ID = "wl-l1"
L2_WL_ID = "wl-l2"


# ── tests ─────────────────────────────────────────────────────────────────────

def test_custom_watchlist_with_pool_and_filters_identified_as_pool_gatekeeper():
    """A 'custom' watchlist with source_pool_id + filter conditions is
    a POOL gatekeeper and must appear in pool_wl_id_map."""
    wl_rows = [
        {
            "id": POOL_WL_ID,
            "user_id": USER_ID,
            "level": "custom",
            "source_pool_id": POOL_ID,
            "profile_config": POOL_PROFILE_CONFIG,
        }
    ]
    mapping = _build_pool_wl_id_map(wl_rows)
    assert (USER_ID, POOL_ID) in mapping
    assert mapping[(USER_ID, POOL_ID)] == POOL_WL_ID


def test_l1_watchlist_without_gate_uses_raw_pool():
    """When no POOL gatekeeper exists, L1 with source_pool_id should read the
    raw pool directly (gate_wl_id is None)."""
    wl_rows = [
        {
            "id": L1_WL_ID,
            "user_id": USER_ID,
            "level": "L1",
            "source_pool_id": POOL_ID,
            "profile_config": L1_PROFILE_CONFIG,
        }
    ]
    mapping = _build_pool_wl_id_map(wl_rows)
    assert len(mapping) == 0

    gate = _resolve_gate_wl_id(wl_rows[0], mapping)
    assert gate is None, "Should read raw pool when no POOL gatekeeper exists"


def test_l1_watchlist_with_same_pool_gate_gets_gate_enforced():
    """When a POOL gatekeeper watchlist shares the same (user, pool) as an L1
    watchlist, the L1 must consume POOL-approved assets, not raw pool_coins.
    This is the core regression for the OFC_USDT bug."""
    wl_rows = [
        {
            "id": POOL_WL_ID,
            "user_id": USER_ID,
            "level": "custom",           # legacy: stored as custom, promoted to POOL
            "source_pool_id": POOL_ID,
            "profile_config": POOL_PROFILE_CONFIG,
        },
        {
            "id": L1_WL_ID,
            "user_id": USER_ID,
            "level": "L1",
            "source_pool_id": POOL_ID,   # same pool as POOL gatekeeper
            "profile_config": L1_PROFILE_CONFIG,
        },
    ]
    mapping = _build_pool_wl_id_map(wl_rows)
    assert (USER_ID, POOL_ID) in mapping, "POOL gatekeeper must be in the map"

    gate = _resolve_gate_wl_id(wl_rows[1], mapping)
    assert gate == POOL_WL_ID, (
        "L1 sharing the same source_pool_id as a POOL gatekeeper "
        "must use POOL-approved assets to prevent bypass"
    )


def test_pool_gatekeeper_itself_is_not_gated():
    """The POOL watchlist itself must resolve gate_wl_id = None so it reads
    all pool_coins (it IS the filter, not the consumer of another filter)."""
    wl_rows = [
        {
            "id": POOL_WL_ID,
            "user_id": USER_ID,
            "level": "custom",
            "source_pool_id": POOL_ID,
            "profile_config": POOL_PROFILE_CONFIG,
        },
    ]
    mapping = _build_pool_wl_id_map(wl_rows)

    gate = _resolve_gate_wl_id(wl_rows[0], mapping)
    assert gate is None, "POOL gatekeeper must read raw pool_coins, not itself"


def test_l1_and_l2_both_gated_when_pool_gatekeeper_exists():
    """Both L1 (source_pool_id) and L2 (source_pool_id, same pool) get the
    gate enforced.  This covers deeper misconfigurations."""
    wl_rows = [
        {
            "id": POOL_WL_ID,
            "user_id": USER_ID,
            "level": "custom",
            "source_pool_id": POOL_ID,
            "profile_config": POOL_PROFILE_CONFIG,
        },
        {
            "id": L1_WL_ID,
            "user_id": USER_ID,
            "level": "L1",
            "source_pool_id": POOL_ID,
            "profile_config": L1_PROFILE_CONFIG,
        },
        {
            "id": L2_WL_ID,
            "user_id": USER_ID,
            "level": "L2",
            "source_pool_id": POOL_ID,
            "profile_config": {},
        },
    ]
    mapping = _build_pool_wl_id_map(wl_rows)

    gate_l1 = _resolve_gate_wl_id(wl_rows[1], mapping)
    gate_l2 = _resolve_gate_wl_id(wl_rows[2], mapping)

    assert gate_l1 == POOL_WL_ID
    assert gate_l2 == POOL_WL_ID


def test_gate_not_enforced_across_different_users():
    """A POOL gatekeeper owned by user-A must NOT gate watchlists owned by
    user-B even if they reference the same pool_id."""
    wl_rows = [
        {
            "id": POOL_WL_ID,
            "user_id": "user-A",
            "level": "custom",
            "source_pool_id": POOL_ID,
            "profile_config": POOL_PROFILE_CONFIG,
        },
        {
            "id": L1_WL_ID,
            "user_id": "user-B",
            "level": "L1",
            "source_pool_id": POOL_ID,
            "profile_config": L1_PROFILE_CONFIG,
        },
    ]
    mapping = _build_pool_wl_id_map(wl_rows)

    gate = _resolve_gate_wl_id(wl_rows[1], mapping)
    assert gate is None, "Gate from user-A must not apply to user-B's watchlist"


def test_gate_not_enforced_across_different_pools():
    """A POOL gatekeeper on pool-A must NOT gate an L1 watchlist on pool-B."""
    wl_rows = [
        {
            "id": POOL_WL_ID,
            "user_id": USER_ID,
            "level": "custom",
            "source_pool_id": "pool-A",
            "profile_config": POOL_PROFILE_CONFIG,
        },
        {
            "id": L1_WL_ID,
            "user_id": USER_ID,
            "level": "L1",
            "source_pool_id": "pool-B",
            "profile_config": L1_PROFILE_CONFIG,
        },
    ]
    mapping = _build_pool_wl_id_map(wl_rows)

    gate = _resolve_gate_wl_id(wl_rows[1], mapping)
    assert gate is None, "Gate from pool-A must not apply to watchlist on pool-B"


def test_custom_watchlist_without_filters_is_not_a_pool_gatekeeper():
    """A custom watchlist with source_pool_id but NO filter conditions is a
    pure monitoring board — it must NOT be treated as a POOL gatekeeper."""
    wl_rows = [
        {
            "id": POOL_WL_ID,
            "user_id": USER_ID,
            "level": "custom",
            "source_pool_id": POOL_ID,
            "profile_config": {"filters": {"conditions": []}},  # no conditions
        },
        {
            "id": L1_WL_ID,
            "user_id": USER_ID,
            "level": "L1",
            "source_pool_id": POOL_ID,
            "profile_config": L1_PROFILE_CONFIG,
        },
    ]
    mapping = _build_pool_wl_id_map(wl_rows)
    assert len(mapping) == 0, "Monitoring board must not be treated as POOL gatekeeper"

    gate = _resolve_gate_wl_id(wl_rows[1], mapping)
    assert gate is None
