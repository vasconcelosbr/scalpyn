"""R3 (retreino nº 2, 2026-07-05) — TIMEOUT precede TP/SL para trades vencidos.

Trade com elapsed >= timeout_candles NUNCA fecha por barreira a preço
corrente: sem candles 1m, o cruzamento histórico é indeterminável e um
"TP corrente" pós-janela inflaria win-rate/EV (F2 do encerramento da fase:
TP_HITs com holding 28-31h).
"""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models.shadow_trade import ShadowTrade  # noqa: E402
from app.tasks.shadow_trade_monitor import (  # noqa: E402
    _finalize_outcome,
    _resolve_expired_timeout,
)

NOW = datetime(2026, 7, 5, 12, 0, 0, tzinfo=timezone.utc)


def _make_shadow(age_minutes: float, timeout_candles: int = 1440) -> ShadowTrade:
    s = ShadowTrade()
    s.symbol = "SYN_USDT"
    s.entry_price = 100.0
    s.tp_price = 101.5
    s.sl_price = 98.5
    s.amount_usdt = 1000.0
    s.timeout_candles = timeout_candles
    s.entry_timestamp = NOW - timedelta(minutes=age_minutes)
    return s


class TestExpiredTimeoutPrecedence:
    def test_expired_trade_with_current_price_above_tp_is_timeout(self):
        """Vencido (28h > 24h) com preço corrente ≥ TP → outcome TIMEOUT, não TP_HIT."""
        shadow = _make_shadow(age_minutes=28 * 60)  # 28h > 1440 min
        mm_price = 102.0  # acima do TP (101.5)
        exit_price = _resolve_expired_timeout(shadow, mm_price, None, 100.0, NOW)
        assert exit_price is not None, "trade vencido deve resolver como expirado"
        _finalize_outcome(shadow, "TIMEOUT", exit_price, NOW, 100.0)
        assert shadow.outcome == "TIMEOUT"
        assert shadow.exit_price == 102.0  # preço corrente, não tp_price

    def test_expired_trade_with_current_price_below_sl_is_timeout(self):
        """Vencido com preço corrente ≤ SL → TIMEOUT (mesma regra, lado SL)."""
        shadow = _make_shadow(age_minutes=30 * 60)
        exit_price = _resolve_expired_timeout(shadow, 97.0, None, 100.0, NOW)
        assert exit_price is not None
        _finalize_outcome(shadow, "TIMEOUT", exit_price, NOW, 100.0)
        assert shadow.outcome == "TIMEOUT"

    def test_non_expired_trade_returns_none_live_close_preserved(self):
        """Não vencido (2h) → None: o live-close TP/SL segue valendo."""
        shadow = _make_shadow(age_minutes=120)
        assert _resolve_expired_timeout(shadow, 102.0, None, 100.0, NOW) is None

    def test_exact_boundary_is_expired(self):
        """elapsed == timeout_candles → vencido (>=, coerente com timeout-elapsed)."""
        shadow = _make_shadow(age_minutes=1440)
        assert _resolve_expired_timeout(shadow, 100.5, None, 100.0, NOW) is not None

    def test_exit_price_precedence_mm_then_ohlcv_then_entry(self):
        """Precedência de exit price: mm > ohlcv > entry (mesma do timeout-elapsed)."""
        shadow = _make_shadow(age_minutes=2000)
        assert _resolve_expired_timeout(shadow, 101.0, 99.0, 100.0, NOW) == 101.0
        assert _resolve_expired_timeout(shadow, None, 99.0, 100.0, NOW) == 99.0
        assert _resolve_expired_timeout(shadow, None, None, 100.0, NOW) == 100.0

    def test_no_timeout_candles_returns_none(self):
        """Sem timeout_candles configurado → não há conceito de vencido aqui."""
        shadow = _make_shadow(age_minutes=5000, timeout_candles=0)
        assert _resolve_expired_timeout(shadow, 102.0, None, 100.0, NOW) is None

    def test_naive_entry_timestamp_normalized(self):
        """entry_timestamp naive é tratado como UTC (não levanta TypeError)."""
        shadow = _make_shadow(age_minutes=2000)
        shadow.entry_timestamp = shadow.entry_timestamp.replace(tzinfo=None)
        assert _resolve_expired_timeout(shadow, 102.0, None, 100.0, NOW) is not None
