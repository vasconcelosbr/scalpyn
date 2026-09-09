from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.schemas.pump_radar import PumpRadarConfig
from app.services.pump_radar_detector import RadarCandle, candles_available_at, detect_pumps, select_snapshot_source


BASE = datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc)


def candle(minutes: int, open_: str, high: str, low: str, close: str, *, available: bool = True) -> RadarCandle:
    opened = BASE + timedelta(minutes=minutes)
    closed = opened + timedelta(minutes=5)
    return RadarCandle(
        open_time=opened,
        close_time=closed,
        open=Decimal(open_),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        available_at=closed + timedelta(seconds=2) if available else None,
    )


def test_detector_uses_lowest_open_and_oldest_tie() -> None:
    rows = [
        candle(0, "100", "101", "99", "100"),
        candle(5, "98", "99", "97", "98"),
        candle(10, "98", "100", "97", "99"),
        candle(15, "100", "102", "99", "102"),
        candle(20, "102", "105", "101", "104"),
        candle(25, "104", "106", "100", "101"),
    ]
    events = detect_pumps(rows, PumpRadarConfig())
    assert len(events) == 1
    assert events[0].start_at == BASE + timedelta(minutes=5)
    assert events[0].confirmed_market_at == BASE + timedelta(minutes=25)
    assert events[0].peak_price == Decimal("106")


def test_gap_breaks_sequence_and_marks_confirmed_event_incomplete() -> None:
    rows = [
        candle(0, "100", "101", "99", "100"),
        candle(5, "100", "106", "100", "105"),
        candle(15, "105", "108", "104", "107"),
    ]
    events = detect_pumps(rows, PumpRadarConfig())
    assert len(events) == 1
    assert events[0].is_incomplete is True
    assert events[0].peak_price == Decimal("106")


def test_backfill_without_availability_is_reconstructed() -> None:
    rows = [
        candle(0, "100", "101", "99", "100", available=False),
        candle(5, "100", "106", "99", "105", available=False),
    ]
    event = detect_pumps(rows, PumpRadarConfig())[0]
    assert event.detected_at is None
    assert event.reconstruction_status == "RECONSTRUCTED"


def test_selected_1235_cannot_see_1235_to_1240_close() -> None:
    rows = [candle(30, "100", "101", "99", "100"), candle(35, "100", "109", "99", "108")]
    selected = BASE + timedelta(minutes=35, seconds=2)
    visible = candles_available_at(rows, selected)
    assert [row.open_time for row in visible] == [BASE + timedelta(minutes=30)]
    assert detect_pumps(visible, PumpRadarConfig()) == []


def test_snapshot_priority_is_fail_closed() -> None:
    assert select_snapshot_source(["FEATURE_ENGINE_RECONSTRUCTION", "EVALUATION_ENVELOPE"]) == "EVALUATION_ENVELOPE"
    assert select_snapshot_source([]) == "UNAVAILABLE"


def test_retracement_ends_event_at_first_closed_candle() -> None:
    rows = [
        candle(0, "100", "101", "99", "100"),
        candle(5, "100", "105", "100", "104"),
        candle(10, "104", "110", "104", "109"),
        candle(15, "109", "109", "105", "106"),
        candle(20, "106", "107", "105", "106"),
    ]
    event = detect_pumps(rows, PumpRadarConfig())[0]
    assert event.peak_price == Decimal("110")
    assert event.end_at == BASE + timedelta(minutes=20)


def test_detector_is_deterministic_across_midnight() -> None:
    midnight_base = datetime(2026, 9, 8, 23, 50, tzinfo=timezone.utc)
    rows = [
        RadarCandle(midnight_base, midnight_base + timedelta(minutes=5), Decimal("100"), Decimal("101"), Decimal("99"), Decimal("100"), available_at=midnight_base + timedelta(minutes=5, seconds=1)),
        RadarCandle(midnight_base + timedelta(minutes=5), midnight_base + timedelta(minutes=10), Decimal("100"), Decimal("106"), Decimal("100"), Decimal("105"), available_at=midnight_base + timedelta(minutes=10, seconds=1)),
    ]
    first = detect_pumps(rows, PumpRadarConfig())
    second = detect_pumps(reversed(rows), PumpRadarConfig())
    assert first == second
    assert first[0].confirmed_market_at.date().isoformat() == "2026-09-09"
