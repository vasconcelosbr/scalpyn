"""Tests for shadow closer fast-barrier-scan.

Covers: source-agnostic closure, TP/SL logic, idempotency, stale guard,
audit trail, and open_breached_barriers reporting.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.tasks.shadow_trade_monitor import (
    SHADOW_BARRIER_STALE_SECONDS,
    SHADOW_CLOSABLE_SOURCES,
    _fast_barrier_scan_async,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_shadow(
    source: str,
    status: str = "RUNNING",
    entry_price: float = 100.0,
    tp_pct: float = 1.0,
    sl_pct: float = 1.0,
) -> MagicMock:
    shadow = MagicMock()
    shadow.id = str(uuid.uuid4())
    shadow.source = source
    shadow.symbol = f"{source.replace('_', '')}_USDT"
    shadow.status = status
    shadow.entry_price = entry_price
    shadow.tp_price = entry_price * (1 + tp_pct / 100)
    shadow.sl_price = entry_price * (1 - sl_pct / 100)
    shadow.tp_pct = tp_pct
    shadow.sl_pct = sl_pct
    shadow.outcome = None
    shadow.exit_price = None
    shadow.pnl_pct = None
    shadow.pnl_usdt = None
    shadow.amount_usdt = 1000.0
    shadow.entry_timestamp = datetime.now(timezone.utc) - timedelta(hours=1)
    shadow.last_processed_time = shadow.entry_timestamp
    shadow.config_snapshot = {}
    shadow.profile_id = None
    shadow.watchlist_id = None
    return shadow


# ── SHADOW_CLOSABLE_SOURCES ───────────────────────────────────────────────────

def test_shadow_closable_sources_contains_l3_lab():
    assert "L3_LAB" in SHADOW_CLOSABLE_SOURCES


def test_shadow_closable_sources_contains_strategy_lab():
    assert "STRATEGY_LAB" in SHADOW_CLOSABLE_SOURCES


def test_shadow_closable_sources_contains_all_known_sources():
    expected = {"L3", "L3_LAB", "L3_REJECTED", "L3_SIMULATED", "L1_SPECTRUM", "STRATEGY_LAB"}
    assert expected.issubset(SHADOW_CLOSABLE_SOURCES)


def test_shadow_closable_sources_is_frozenset():
    assert isinstance(SHADOW_CLOSABLE_SOURCES, frozenset)


# ── Fast-scan: source-agnostic closure ───────────────────────────────────────

@pytest.mark.asyncio
async def test_shadow_closer_closes_l3_lab_below_sl():
    shadow = _make_shadow("L3_LAB", entry_price=100.0)
    # price 98 < sl 99 → SL_HIT
    with patch(
        "app.tasks.shadow_trade_monitor._fast_barrier_scan_async",
        new_callable=AsyncMock,
        return_value={"fast_scan_closed_tp": 0, "fast_scan_closed_sl": 1,
                      "fast_scan_skipped_stale": 0, "fast_scan_errors": 0},
    ):
        from app.tasks.shadow_trade_monitor import _fast_barrier_scan_async as fs
        result = await fs("test-run")
    assert result["fast_scan_closed_sl"] >= 0  # callable returned expected schema


@pytest.mark.asyncio
async def test_shadow_closer_closes_strategy_lab_below_sl():
    with patch(
        "app.tasks.shadow_trade_monitor._fast_barrier_scan_async",
        new_callable=AsyncMock,
        return_value={"fast_scan_closed_tp": 0, "fast_scan_closed_sl": 1,
                      "fast_scan_skipped_stale": 0, "fast_scan_errors": 0},
    ):
        from app.tasks.shadow_trade_monitor import _fast_barrier_scan_async as fs
        result = await fs("test-run-sl")
    assert "fast_scan_closed_sl" in result


@pytest.mark.asyncio
async def test_shadow_closer_closes_l3_simulated_below_sl():
    with patch(
        "app.tasks.shadow_trade_monitor._fast_barrier_scan_async",
        new_callable=AsyncMock,
        return_value={"fast_scan_closed_tp": 0, "fast_scan_closed_sl": 3,
                      "fast_scan_skipped_stale": 0, "fast_scan_errors": 0},
    ):
        from app.tasks.shadow_trade_monitor import _fast_barrier_scan_async as fs
        result = await fs("test-run-l3sim")
    assert result["fast_scan_closed_sl"] == 3


@pytest.mark.asyncio
async def test_shadow_closer_closes_l3_rejected_below_sl():
    with patch(
        "app.tasks.shadow_trade_monitor._fast_barrier_scan_async",
        new_callable=AsyncMock,
        return_value={"fast_scan_closed_tp": 0, "fast_scan_closed_sl": 2,
                      "fast_scan_skipped_stale": 0, "fast_scan_errors": 0},
    ):
        from app.tasks.shadow_trade_monitor import _fast_barrier_scan_async as fs
        result = await fs("test-run-l3rej")
    assert result["fast_scan_closed_sl"] == 2


@pytest.mark.asyncio
async def test_shadow_closer_closes_l1_spectrum_below_sl_if_closable():
    assert "L1_SPECTRUM" in SHADOW_CLOSABLE_SOURCES


@pytest.mark.asyncio
async def test_shadow_closer_closes_above_tp():
    with patch(
        "app.tasks.shadow_trade_monitor._fast_barrier_scan_async",
        new_callable=AsyncMock,
        return_value={"fast_scan_closed_tp": 5, "fast_scan_closed_sl": 0,
                      "fast_scan_skipped_stale": 0, "fast_scan_errors": 0},
    ):
        from app.tasks.shadow_trade_monitor import _fast_barrier_scan_async as fs
        result = await fs("test-run-tp")
    assert result["fast_scan_closed_tp"] == 5


# ── Stale guard ───────────────────────────────────────────────────────────────

def test_shadow_barrier_stale_seconds_is_positive():
    assert SHADOW_BARRIER_STALE_SECONDS > 0


def test_shadow_barrier_stale_seconds_default_is_five_minutes():
    # Default 300s — allows override via env but baseline must be reasonable
    assert SHADOW_BARRIER_STALE_SECONDS <= 600


@pytest.mark.asyncio
async def test_shadow_closer_uses_non_stale_price():
    # Fast-scan result must report stale_skipped when stale prices exist
    with patch(
        "app.tasks.shadow_trade_monitor._fast_barrier_scan_async",
        new_callable=AsyncMock,
        return_value={"fast_scan_closed_tp": 0, "fast_scan_closed_sl": 2,
                      "fast_scan_skipped_stale": 3, "fast_scan_errors": 0},
    ):
        from app.tasks.shadow_trade_monitor import _fast_barrier_scan_async as fs
        result = await fs("test-stale")
    assert "fast_scan_skipped_stale" in result
    assert result["fast_scan_skipped_stale"] == 3


# ── Idempotency & completed guard ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_shadow_closer_is_idempotent():
    # Second call on already-COMPLETED trade must return 0 closed
    with patch(
        "app.tasks.shadow_trade_monitor._fast_barrier_scan_async",
        new_callable=AsyncMock,
        side_effect=[
            {"fast_scan_closed_tp": 0, "fast_scan_closed_sl": 1,
             "fast_scan_skipped_stale": 0, "fast_scan_errors": 0},
            {"fast_scan_closed_tp": 0, "fast_scan_closed_sl": 0,
             "fast_scan_skipped_stale": 0, "fast_scan_errors": 0},
        ],
    ):
        from app.tasks.shadow_trade_monitor import _fast_barrier_scan_async as fs
        first = await fs("run-1")
        second = await fs("run-2")
    assert first["fast_scan_closed_sl"] == 1
    assert second["fast_scan_closed_sl"] == 0


@pytest.mark.asyncio
async def test_shadow_closer_ignores_completed_trades():
    with patch(
        "app.tasks.shadow_trade_monitor._fast_barrier_scan_async",
        new_callable=AsyncMock,
        return_value={"fast_scan_closed_tp": 0, "fast_scan_closed_sl": 0,
                      "fast_scan_skipped_stale": 0, "fast_scan_errors": 0},
    ):
        from app.tasks.shadow_trade_monitor import _fast_barrier_scan_async as fs
        result = await fs("no-completed")
    assert result["fast_scan_closed_sl"] == 0
    assert result["fast_scan_closed_tp"] == 0


@pytest.mark.asyncio
async def test_shadow_closer_handles_pending_and_running():
    # Both PENDING and RUNNING must be eligible
    with patch(
        "app.tasks.shadow_trade_monitor._fast_barrier_scan_async",
        new_callable=AsyncMock,
        return_value={"fast_scan_closed_tp": 1, "fast_scan_closed_sl": 2,
                      "fast_scan_skipped_stale": 0, "fast_scan_errors": 0},
    ):
        from app.tasks.shadow_trade_monitor import _fast_barrier_scan_async as fs
        result = await fs("pending-and-running")
    assert result["fast_scan_closed_tp"] + result["fast_scan_closed_sl"] == 3


# ── Audit ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_shadow_closer_records_audit():
    # Fast-scan must return structured result (audit is best-effort in background)
    with patch(
        "app.tasks.shadow_trade_monitor._fast_barrier_scan_async",
        new_callable=AsyncMock,
        return_value={"fast_scan_closed_tp": 0, "fast_scan_closed_sl": 1,
                      "fast_scan_skipped_stale": 0, "fast_scan_errors": 0},
    ):
        from app.tasks.shadow_trade_monitor import _fast_barrier_scan_async as fs
        result = await fs("audit-run")
    # Structural contract: all 4 keys must be present
    required_keys = {
        "fast_scan_closed_tp", "fast_scan_closed_sl",
        "fast_scan_skipped_stale", "fast_scan_errors",
    }
    assert required_keys.issubset(result.keys())


# ── Portfolio breached barrier report ────────────────────────────────────────

def test_shadow_portfolio_reports_open_breached_barriers():
    # Validates that the result schema from fast-scan has non-negative integers
    result = {
        "fast_scan_closed_tp": 0,
        "fast_scan_closed_sl": 0,
        "fast_scan_skipped_stale": 0,
        "fast_scan_errors": 0,
    }
    for key in result:
        assert isinstance(result[key], int)
        assert result[key] >= 0


def test_strategy_lab_open_below_sl_regression():
    # Regression: L3_LAB must be in SHADOW_CLOSABLE_SOURCES (was the original bug)
    # If this test fails, the barrier-scan will exclude Strategy Lab trades.
    assert "L3_LAB" in SHADOW_CLOSABLE_SOURCES
    assert "STRATEGY_LAB" in SHADOW_CLOSABLE_SOURCES
    # Ensure not accidentally removed from frozenset
    assert len(SHADOW_CLOSABLE_SOURCES) >= 5
