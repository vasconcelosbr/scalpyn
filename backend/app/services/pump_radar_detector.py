from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Iterable, Sequence

from ..schemas.pump_radar import PumpRadarConfig


@dataclass(frozen=True)
class RadarCandle:
    open_time: datetime
    close_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume_base: Decimal = Decimal("0")
    available_at: datetime | None = None
    is_closed: bool = True


@dataclass(frozen=True)
class PumpEvent:
    start_at: datetime
    confirmed_market_at: datetime
    detected_at: datetime | None
    peak_at: datetime
    end_at: datetime | None
    start_price: Decimal
    peak_price: Decimal
    end_price: Decimal | None
    rise_pct: Decimal
    retracement_pct: Decimal | None
    is_incomplete: bool
    reconstruction_status: str


def candles_available_at(candles: Iterable[RadarCandle], selected_at: datetime) -> list[RadarCandle]:
    """Closed-candle, point-in-time view. A candle closing after T is future data."""
    return sorted(
        (
            candle
            for candle in candles
            if candle.is_closed
            and candle.close_time <= selected_at
            and (candle.available_at is None or candle.available_at <= selected_at)
        ),
        key=lambda candle: candle.open_time,
    )


def _split_contiguous(candles: Sequence[RadarCandle], timeframe_minutes: int = 5) -> list[list[RadarCandle]]:
    if not candles:
        return []
    expected = timedelta(minutes=timeframe_minutes)
    segments: list[list[RadarCandle]] = [[candles[0]]]
    for candle in candles[1:]:
        if candle.open_time - segments[-1][-1].open_time > expected:
            segments.append([])
        segments[-1].append(candle)
    return segments


def _first_event(segment: Sequence[RadarCandle], start_index: int, config: PumpRadarConfig) -> tuple[PumpEvent | None, int]:
    threshold = Decimal(str(config.minimum_rise_pct)) / Decimal("100")
    max_window = timedelta(minutes=config.maximum_window_minutes)
    confirmation_index = -1
    base_index = -1

    for current_index in range(start_index, len(segment)):
        current = segment[current_index]
        window_start = current.open_time - max_window
        candidates = [
            (index, candle)
            for index, candle in enumerate(segment[start_index : current_index + 1], start=start_index)
            if candle.open_time >= window_start
        ]
        if not candidates:
            continue
        # Python's stable min preserves the oldest candle when opens tie.
        candidate_index, candidate = min(candidates, key=lambda item: (item[1].open, item[1].open_time))
        if current.high >= candidate.open * (Decimal("1") + threshold):
            confirmation_index = current_index
            base_index = candidate_index
            break

    if confirmation_index < 0:
        return None, len(segment)

    base = segment[base_index]
    confirmation = segment[confirmation_index]
    peak = confirmation
    peak_price = confirmation.high
    end: RadarCandle | None = None
    retracement: Decimal | None = None
    incomplete = False
    max_duration = timedelta(minutes=config.maximum_duration_minutes)
    no_high_timeout = timedelta(minutes=config.no_new_high_minutes)
    retrace_fraction = Decimal(str(config.retracement_pct)) / Decimal("100")

    for index in range(confirmation_index, len(segment)):
        candle = segment[index]
        made_new_high = candle.high > peak_price
        if made_new_high:
            peak = candle
            peak_price = candle.high

        gain = peak_price - base.open
        retrace_level = peak_price - (gain * retrace_fraction)
        # A candle that both sets a new high and trades below the retracement
        # level has unknown intrabar order in OHLCV. Do not invent an exit;
        # wait for the first later closed candle that confirms the retracement.
        reached_retrace = gain > 0 and not made_new_high and candle.low <= retrace_level and index > confirmation_index
        no_new_high = candle.close_time - peak.close_time >= no_high_timeout
        duration_limit = candle.close_time - base.open_time >= max_duration
        if reached_retrace or no_new_high or duration_limit:
            end = candle
            if gain > 0:
                retracement = ((peak_price - candle.close) / gain) * Decimal("100")
            break

    if end is None:
        end = segment[-1]
        incomplete = True

    availability = [c.available_at for c in segment[base_index : confirmation_index + 1]]
    detected_at = max(availability) if availability and all(availability) else None
    reconstruction_status = "RECORDED" if detected_at is not None else "RECONSTRUCTED"
    rise_pct = ((peak_price / base.open) - Decimal("1")) * Decimal("100")
    event = PumpEvent(
        start_at=base.open_time,
        confirmed_market_at=confirmation.close_time,
        detected_at=detected_at,
        peak_at=peak.open_time,
        end_at=end.close_time if end else None,
        start_price=base.open,
        peak_price=peak_price,
        end_price=end.close if end else None,
        rise_pct=rise_pct,
        retracement_pct=retracement,
        is_incomplete=incomplete,
        reconstruction_status=reconstruction_status,
    )
    return event, max(confirmation_index + 1, segment.index(end) + 1 if end else confirmation_index + 1)


def _merge_events(events: Sequence[PumpEvent], merge_gap_minutes: int) -> list[PumpEvent]:
    if not events:
        return []
    merged = [events[0]]
    gap = timedelta(minutes=merge_gap_minutes)
    for event in events[1:]:
        previous = merged[-1]
        previous_end = previous.end_at or previous.confirmed_market_at
        if event.start_at <= previous_end + gap:
            peak_source = event if event.peak_price > previous.peak_price else previous
            end_source = event if (event.end_at or event.confirmed_market_at) >= previous_end else previous
            merged[-1] = replace(
                previous,
                confirmed_market_at=min(previous.confirmed_market_at, event.confirmed_market_at),
                detected_at=(
                    None
                    if previous.detected_at is None or event.detected_at is None
                    else min(previous.detected_at, event.detected_at)
                ),
                peak_at=peak_source.peak_at,
                peak_price=peak_source.peak_price,
                end_at=end_source.end_at,
                end_price=end_source.end_price,
                rise_pct=((peak_source.peak_price / previous.start_price) - Decimal("1")) * Decimal("100"),
                retracement_pct=end_source.retracement_pct,
                is_incomplete=previous.is_incomplete or event.is_incomplete,
                reconstruction_status=(
                    "RECONSTRUCTED"
                    if previous.reconstruction_status == "RECONSTRUCTED" or event.reconstruction_status == "RECONSTRUCTED"
                    else "RECORDED"
                ),
            )
        else:
            merged.append(event)
    return merged


def detect_pumps(candles: Iterable[RadarCandle], config: PumpRadarConfig) -> list[PumpEvent]:
    ordered = sorted((c for c in candles if c.is_closed), key=lambda candle: candle.open_time)
    raw_events: list[PumpEvent] = []
    for segment in _split_contiguous(ordered):
        cursor = 0
        while cursor < len(segment):
            event, next_cursor = _first_event(segment, cursor, config)
            if event is None:
                break
            raw_events.append(event)
            cursor = max(next_cursor, cursor + 1)
    return _merge_events(raw_events, config.merge_gap_minutes)


SNAPSHOT_PRIORITY = (
    "DECISION_SNAPSHOT",
    "EVALUATION_ENVELOPE",
    "FEATURE_ENGINE_RECONSTRUCTION",
    "UNAVAILABLE",
)


def select_snapshot_source(available_sources: Iterable[str]) -> str:
    candidates = set(available_sources)
    return next(source for source in SNAPSHOT_PRIORITY if source in candidates or source == "UNAVAILABLE")
