"""Fix 2026-08: MAE/MFE subestimado no live-close path (Path B).

Causa raiz: ``_get_current_price_multi_tf`` só lê ``close`` de ``ohlcv`` —
símbolos sem candle 1m (só 5m/15m/30m) tinham ``min/max_price_post_entry``
atualizados exclusivamente pelo close, perdendo o high/low real da candle
(sintoma: MFE=0 com MAE != 0, ou vice-versa, quando o preço varreu bem além
do close antes de fechar).

Corrigido com uma função irmã, ``_get_current_ohlc_multi_tf``, usada
SOMENTE para alimentar o tracking de extremos — não participa do
candidates[]/TP-SL crossing check, que permanece baseado em close (mesma
política conservadora documentada em ``_advance_shadow``).
"""
import sys
import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.shadow_trade_service import _get_current_ohlc_multi_tf
from app.tasks import shadow_trade_monitor
from app.tasks.shadow_trade_monitor import _advance_shadow


# ── _get_current_ohlc_multi_tf: leitura pura de high/low ────────────────────

class _Row:
    def __init__(self, high, low, time):
        self.high = high
        self.low = low
        self.time = time


class _Result:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _DB:
    def __init__(self, row):
        self._result = _Result(row)
        self.statement = ""
        self.params = None

    async def execute(self, statement, params):
        self.statement = str(statement)
        self.params = params
        return self._result


@pytest.mark.asyncio
async def test_get_current_ohlc_multi_tf_returns_high_low():
    ts = datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc)
    db = _DB(_Row(high=105.0, low=94.0, time=ts))

    high, low, out_ts = await _get_current_ohlc_multi_tf(db, "BTC_USDT")

    assert (high, low, out_ts) == (105.0, 94.0, ts)
    assert "SELECT high, low, time" in db.statement
    assert "timeframe IN ('1m', '5m', '15m', '30m')" in db.statement


@pytest.mark.asyncio
async def test_get_current_ohlc_multi_tf_none_when_no_row():
    db = _DB(None)
    assert await _get_current_ohlc_multi_tf(db, "BTC_USDT") == (None, None, None)


@pytest.mark.asyncio
async def test_get_current_ohlc_multi_tf_none_when_high_or_low_null():
    ts = datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc)
    db = _DB(_Row(high=None, low=94.0, time=ts))
    assert await _get_current_ohlc_multi_tf(db, "BTC_USDT") == (None, None, None)


# ── _advance_shadow: MAE/MFE ampliado com high/low real (Path B) ────────────

def _make_shadow(entry=100.0, tp=110.0, sl=90.0, entry_ts=None):
    entry_ts = entry_ts or datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc)
    return SimpleNamespace(
        id="shadow-test-id",
        symbol="TEST_USDT",
        status="RUNNING",
        entry_price=entry,
        entry_timestamp=entry_ts,
        tp_price=tp,
        sl_price=sl,
        tp_pct=10.0,
        sl_pct=10.0,
        barrier_mode="FIXED",
        tp_pct_applied=10.0,
        sl_pct_applied=10.0,
        atr_pct_at_entry=1.0,
        min_price_post_entry=None,
        max_price_post_entry=None,
        mae_at=None,
        mfe_at=None,
        last_processed_time=entry_ts,
        created_at=entry_ts,
        timeout_candles=None,
        ttt_enabled=False,
        ttt_tp_pct=None,
    )


@pytest.mark.asyncio
async def test_live_close_widens_mae_mfe_with_real_high_low(monkeypatch):
    """Path B: close fica no meio da faixa, mas high/low da candle vão além.

    Antes do fix, min/max_price_post_entry ficavam presos ao close (100.2)
    — o teste falharia. Depois do fix, refletem o high/low reais da candle
    (105.0 / 94.0), sem cruzar TP(110)/SL(90) — outcome permanece RUNNING.
    """
    ts = datetime(2026, 7, 12, 12, 1, tzinfo=timezone.utc)
    shadow = _make_shadow()

    monkeypatch.setattr(
        shadow_trade_monitor.shadow_trade_service,
        "_get_market_metadata_price",
        AsyncMock(return_value=(None, None)),
    )
    monkeypatch.setattr(
        shadow_trade_monitor.shadow_trade_service,
        "_get_current_price_multi_tf",
        AsyncMock(return_value=(100.2, ts)),
    )
    monkeypatch.setattr(
        shadow_trade_monitor.shadow_trade_service,
        "_get_current_ohlc_multi_tf",
        AsyncMock(return_value=(105.0, 94.0, ts)),
    )
    monkeypatch.setattr(
        shadow_trade_monitor, "_fetch_candles", AsyncMock(return_value=[])
    )

    result = await _advance_shadow(db=object(), shadow=shadow)

    assert result == "running"
    assert shadow.max_price_post_entry == pytest.approx(105.0)
    assert shadow.min_price_post_entry == pytest.approx(94.0)


@pytest.mark.asyncio
async def test_live_close_ignores_stale_ohlc_before_entry(monkeypatch):
    """Skew guard: high/low de candle anterior ao entry_timestamp é descartado."""
    entry_ts = datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc)
    stale_ts = entry_ts - timedelta(minutes=5)
    shadow = _make_shadow(entry_ts=entry_ts)

    monkeypatch.setattr(
        shadow_trade_monitor.shadow_trade_service,
        "_get_market_metadata_price",
        AsyncMock(return_value=(None, None)),
    )
    monkeypatch.setattr(
        shadow_trade_monitor.shadow_trade_service,
        "_get_current_price_multi_tf",
        AsyncMock(return_value=(100.2, entry_ts)),
    )
    monkeypatch.setattr(
        shadow_trade_monitor.shadow_trade_service,
        "_get_current_ohlc_multi_tf",
        AsyncMock(return_value=(109.9, 90.1, stale_ts)),
    )
    monkeypatch.setattr(
        shadow_trade_monitor, "_fetch_candles", AsyncMock(return_value=[])
    )

    await _advance_shadow(db=object(), shadow=shadow)

    # high/low descartados (ts < entry_ts) — só o close (100.2) atualiza.
    assert shadow.max_price_post_entry == pytest.approx(100.2)
    assert shadow.min_price_post_entry == pytest.approx(100.2)
