"""Testes para a convenção intrabar de shadow trades (migration 071).

Critério de aceite #3: quando TP e SL são tocados no mesmo candle 1m,
o outcome deve ser SL_HIT (convenção conservadora) e
barrier_touched='BOTH_SAME_CANDLE'.

Também cobre:
- TP-only → barrier_touched='TP'
- SL-only → barrier_touched='SL' (via _finalize_outcome)
- TIMEOUT → barrier_touched='NONE', final_return_pct preenchido
- net_return_pct calculado corretamente quando config tem ml_fee_roundtrip_pct
"""

import sys
import os
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.tasks.shadow_trade_monitor import _finalize_outcome


# ── helpers ──────────────────────────────────────────────────────────────────

def _make_shadow(
    entry_price: float = 1.0,
    tp_pct: float = 1.0,
    sl_pct: float = 1.0,
    amount_usdt: float = 1000.0,
    config_snapshot: Optional[dict] = None,
    barrier_touched: Optional[str] = None,
    barrier_touched_at: Optional[datetime] = None,
    min_price_post_entry: Optional[float] = None,
    max_price_post_entry: Optional[float] = None,
    entry_timestamp: Optional[datetime] = None,
):
    shadow = SimpleNamespace(
        id="test-id",
        symbol="TEST_USDT",
        entry_price=entry_price,
        tp_price=entry_price * (1 + tp_pct / 100.0),
        sl_price=entry_price * (1 - sl_pct / 100.0),
        tp_pct=tp_pct,
        sl_pct=sl_pct,
        amount_usdt=amount_usdt,
        config_snapshot=config_snapshot or {},
        barrier_touched=barrier_touched,
        barrier_touched_at=barrier_touched_at,
        min_price_post_entry=min_price_post_entry,
        max_price_post_entry=max_price_post_entry,
        entry_timestamp=entry_timestamp or datetime(2026, 1, 1, tzinfo=timezone.utc),
        pnl_pct=None,
        pnl_usdt=None,
        holding_seconds=None,
        mae_pct=None,
        mfe_pct=None,
        max_drawdown_pct=None,
        max_profit_pct=None,
        intrabar_convention=None,
        final_return_pct=None,
        net_return_pct=None,
        fee_roundtrip_pct_applied=None,
        status=None,
        completed_at=None,
        last_processed_time=None,
        ttt_enabled=False,
        ttt_tp_pct=None,
        ttt_timeout_minutes=None,
        ttt_outcome=None,
        ttt_close_reason=None,
        ttt_fast_win_bucket=None,
        ttt_analysis_done=None,
        elapsed_minutes=None,
        time_to_tp_minutes=None,
        profit_velocity=None,
        profit_velocity_per_hour=None,
        max_profit_first_15m=None,
        max_profit_first_30m=None,
        max_profit_first_60m=None,
        candles_to_peak=None,
        candles_to_first_positive=None,
    )
    return shadow


_EXIT_TS = datetime(2026, 1, 1, 0, 5, tzinfo=timezone.utc)


# ── critério de aceite #3 — BOTH_SAME_CANDLE ─────────────────────────────────

def test_both_same_candle_resolves_as_sl():
    """TP + SL no mesmo candle → outcome SL_HIT, barrier_touched BOTH_SAME_CANDLE."""
    shadow = _make_shadow()
    shadow.barrier_touched = "BOTH_SAME_CANDLE"
    shadow.barrier_touched_at = _EXIT_TS

    _finalize_outcome(shadow, "SL_HIT", shadow.sl_price, _EXIT_TS, shadow.entry_price)

    assert shadow.status == "COMPLETED"
    assert shadow.barrier_touched == "BOTH_SAME_CANDLE"
    assert shadow.intrabar_convention == "SL_FIRST"
    assert shadow.pnl_pct < 0


def test_both_same_candle_pnl_is_sl_price():
    """PnL no BOTH_SAME_CANDLE = exit_price=sl_price (pior caso)."""
    entry = 100.0
    sl_pct = 1.0
    shadow = _make_shadow(entry_price=entry, sl_pct=sl_pct)
    shadow.barrier_touched = "BOTH_SAME_CANDLE"
    exit_price = shadow.sl_price

    _finalize_outcome(shadow, "SL_HIT", exit_price, _EXIT_TS, entry)

    assert abs(shadow.pnl_pct - (-sl_pct)) < 1e-9


# ── TP-only ───────────────────────────────────────────────────────────────────

def test_tp_only_sets_barrier_tp():
    shadow = _make_shadow()
    shadow.barrier_touched = "TP"
    shadow.barrier_touched_at = _EXIT_TS

    _finalize_outcome(shadow, "TP_HIT", shadow.tp_price, _EXIT_TS, shadow.entry_price)

    assert shadow.barrier_touched == "TP"
    assert shadow.intrabar_convention == "SL_FIRST"
    assert shadow.final_return_pct is None  # só TIMEOUT preenche


# ── SL-only (sem BOTH_SAME_CANDLE) ───────────────────────────────────────────

def test_sl_only_sets_barrier_sl_via_finalize():
    """SL-only: barrier_touched deve ser preenchido como 'SL' por _finalize_outcome."""
    shadow = _make_shadow()
    # barrier_touched = None (não setado no loop)

    _finalize_outcome(shadow, "SL_HIT", shadow.sl_price, _EXIT_TS, shadow.entry_price)

    assert shadow.barrier_touched == "SL"
    assert shadow.barrier_touched_at == _EXIT_TS
    assert shadow.intrabar_convention == "SL_FIRST"


# ── TIMEOUT ───────────────────────────────────────────────────────────────────

def test_timeout_sets_barrier_none_and_final_return():
    entry = 100.0
    close_price = 100.5  # +0.5%
    shadow = _make_shadow(entry_price=entry)

    _finalize_outcome(shadow, "TIMEOUT", close_price, _EXIT_TS, entry)

    assert shadow.barrier_touched == "NONE"
    assert shadow.barrier_touched_at is None
    assert shadow.intrabar_convention == "SL_FIRST"
    assert shadow.final_return_pct is not None
    assert abs(shadow.final_return_pct - 0.5) < 1e-6


# ── net_return_pct (Fase 2) ───────────────────────────────────────────────────

def test_net_return_pct_computed_from_config():
    """net_return_pct = pnl_pct - ml_fee_roundtrip_pct quando config presente."""
    config = {"ml_fee_roundtrip_pct": 0.2}
    shadow = _make_shadow(config_snapshot=config)

    _finalize_outcome(shadow, "TP_HIT", shadow.tp_price, _EXIT_TS, shadow.entry_price)

    assert shadow.fee_roundtrip_pct_applied == pytest.approx(0.2)
    assert shadow.net_return_pct == pytest.approx(shadow.pnl_pct - 0.2)


def test_net_return_pct_null_when_no_config():
    """Sem ml_fee_roundtrip_pct no config → net_return_pct permanece NULL."""
    shadow = _make_shadow(config_snapshot={})

    _finalize_outcome(shadow, "TP_HIT", shadow.tp_price, _EXIT_TS, shadow.entry_price)

    assert shadow.net_return_pct is None
    assert shadow.fee_roundtrip_pct_applied is None


# ── intrabar_convention sempre SL_FIRST ──────────────────────────────────────

@pytest.mark.parametrize("outcome,exit_price_attr", [
    ("TP_HIT", "tp_price"),
    ("SL_HIT", "sl_price"),
    ("TIMEOUT", "entry_price"),
])
def test_intrabar_convention_always_sl_first(outcome, exit_price_attr):
    shadow = _make_shadow()
    exit_price = getattr(shadow, exit_price_attr)

    _finalize_outcome(shadow, outcome, exit_price, _EXIT_TS, shadow.entry_price)

    assert shadow.intrabar_convention == "SL_FIRST"
