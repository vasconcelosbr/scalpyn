from datetime import datetime, timedelta, timezone

import pytest

from app.services.shadow_trade_measurement_service import calculate_measurement


BASE = datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc)


def candle(minutes: int, *, high: float, low: float) -> dict:
    return {"time": BASE + timedelta(minutes=minutes), "high": high, "low": low}


def test_tp_after_drawdown_uses_full_trade_lifetime() -> None:
    result = calculate_measurement(
        entry_price=100.0,
        entry_at=BASE + timedelta(seconds=20),
        exit_price=102.0,
        exit_at=BASE + timedelta(minutes=2, seconds=30),
        timeframe="1m",
        candles=[
            candle(0, high=100.5, low=99.0),
            candle(1, high=101.0, low=98.0),
            candle(2, high=102.2, low=100.5),
        ],
        observed_at=BASE + timedelta(minutes=4),
    )

    assert result.status == "READY"
    assert result.mae_pct == pytest.approx(-2.0)
    assert result.mfe_pct == pytest.approx(2.2)
    assert result.mae_at == BASE + timedelta(minutes=1)
    assert result.entry_boundary_partial is True
    assert result.exit_boundary_partial is True


def test_sl_after_favourable_excursion_keeps_positive_mfe() -> None:
    result = calculate_measurement(
        entry_price=100.0,
        entry_at=BASE,
        exit_price=99.0,
        exit_at=BASE + timedelta(minutes=2, seconds=10),
        timeframe="1m",
        candles=[
            candle(0, high=101.5, low=99.8),
            candle(1, high=103.0, low=100.0),
            candle(2, high=100.0, low=98.8),
        ],
        observed_at=BASE + timedelta(minutes=4),
    )

    assert result.status == "READY"
    assert result.mfe_pct == pytest.approx(3.0)
    assert result.mae_pct == pytest.approx(-1.2)
    assert result.mfe_at == BASE + timedelta(minutes=1)


def test_entry_boundary_cross_is_marked_partial() -> None:
    result = calculate_measurement(
        entry_price=100.0,
        entry_at=BASE + timedelta(seconds=45),
        exit_price=101.0,
        exit_at=BASE + timedelta(minutes=1, seconds=15),
        timeframe="1m",
        candles=[
            candle(0, high=101.2, low=99.5),
            candle(1, high=101.5, low=100.5),
        ],
        observed_at=BASE + timedelta(minutes=3),
    )

    assert result.status == "READY"
    assert result.entry_boundary_partial is True
    assert result.exit_boundary_partial is True
    assert result.mfe_at == BASE + timedelta(minutes=1)


def test_exit_boundary_waits_until_candle_is_closed() -> None:
    result = calculate_measurement(
        entry_price=100.0,
        entry_at=BASE,
        exit_price=101.0,
        exit_at=BASE + timedelta(minutes=1, seconds=15),
        timeframe="1m",
        candles=[candle(0, high=100.5, low=99.5), candle(1, high=101.0, low=100.0)],
        observed_at=BASE + timedelta(minutes=1, seconds=30),
    )

    assert result.status == "PENDING"
    assert result.mae_pct is None
    assert result.unavailable_reason == "EXIT_BOUNDARY_CANDLE_NOT_CLOSED"


def test_missing_ohlcv_is_unavailable_never_zero() -> None:
    result = calculate_measurement(
        entry_price=100.0,
        entry_at=BASE,
        exit_price=101.0,
        exit_at=BASE + timedelta(minutes=1),
        timeframe="1m",
        candles=[],
        observed_at=BASE + timedelta(minutes=3),
    )

    assert result.status == "UNAVAILABLE"
    assert result.mae_pct is None
    assert result.mfe_pct is None
    assert result.source == "unavailable"


def test_input_hash_is_deterministic_and_changes_with_candles() -> None:
    kwargs = dict(
        entry_price=100.0,
        entry_at=BASE,
        exit_price=101.0,
        exit_at=BASE + timedelta(minutes=1),
        timeframe="1m",
        observed_at=BASE + timedelta(minutes=3),
    )
    first = calculate_measurement(candles=[candle(0, high=101.0, low=99.0)], **kwargs)
    again = calculate_measurement(candles=[candle(0, high=101.0, low=99.0)], **kwargs)
    changed = calculate_measurement(candles=[candle(0, high=102.0, low=99.0)], **kwargs)

    assert first.input_hash == again.input_hash
    assert first.input_hash != changed.input_hash
