from datetime import datetime, timedelta, timezone

from app.tasks.radar_auto_discover import (
    RADAR_ABSENCE_GRACE_SECONDS,
    radar_absence_debounce,
)


# 2026-09-25 incident: the radar feed's own top-N selection is noisy
# cycle-to-cycle (observed live: 0-4 symbols out of a ~19-symbol pool per
# ~1min run). Treating a single cycle's absence as "dropped from the radar"
# made held_for_open_position flap for almost every symbol almost every
# cycle, freezing L1/L2/L3 candidacy platform-wide.


def test_symbol_absent_within_grace_window_is_not_removed():
    now = datetime(2026, 9, 25, 20, 40, tzinfo=timezone.utc)
    last_seen = {"BTC_USDT": now - timedelta(seconds=RADAR_ABSENCE_GRACE_SECONDS - 30)}
    result = radar_absence_debounce(
        absent={"BTC_USDT"}, last_seen_by_symbol=last_seen, now=now
    )
    assert result == set()


def test_symbol_absent_past_grace_window_is_removed():
    now = datetime(2026, 9, 25, 20, 40, tzinfo=timezone.utc)
    last_seen = {"BTC_USDT": now - timedelta(seconds=RADAR_ABSENCE_GRACE_SECONDS + 1)}
    result = radar_absence_debounce(
        absent={"BTC_USDT"}, last_seen_by_symbol=last_seen, now=now
    )
    assert result == {"BTC_USDT"}


def test_symbol_absent_exactly_at_grace_boundary_is_removed():
    now = datetime(2026, 9, 25, 20, 40, tzinfo=timezone.utc)
    last_seen = {"BTC_USDT": now - timedelta(seconds=RADAR_ABSENCE_GRACE_SECONDS)}
    result = radar_absence_debounce(
        absent={"BTC_USDT"}, last_seen_by_symbol=last_seen, now=now
    )
    assert result == {"BTC_USDT"}


def test_symbol_never_stamped_is_grace_exempt_and_removed_immediately():
    """A pre-migration row (radar_last_seen_at is None) must not be pinned
    forever just because its true absence duration is unknown."""
    now = datetime(2026, 9, 25, 20, 40, tzinfo=timezone.utc)
    result = radar_absence_debounce(
        absent={"BTC_USDT"}, last_seen_by_symbol={"BTC_USDT": None}, now=now
    )
    assert result == {"BTC_USDT"}


def test_noisy_single_cycle_flapping_does_not_remove_the_whole_pool():
    """Regression for the actual incident: a pool where every symbol was
    seen within the last minute (normal ~1min sync cadence) must produce
    zero removals even when the radar's this-cycle selection is empty."""
    now = datetime(2026, 9, 25, 20, 40, tzinfo=timezone.utc)
    pool = {f"SYM{i}_USDT" for i in range(19)}
    last_seen = {symbol: now - timedelta(seconds=60) for symbol in pool}
    result = radar_absence_debounce(
        absent=pool, last_seen_by_symbol=last_seen, now=now
    )
    assert result == set()


def test_mixed_pool_only_removes_the_genuinely_stale_symbols():
    now = datetime(2026, 9, 25, 20, 40, tzinfo=timezone.utc)
    last_seen = {
        "FRESH_USDT": now - timedelta(seconds=30),
        "STALE_USDT": now - timedelta(seconds=RADAR_ABSENCE_GRACE_SECONDS + 60),
    }
    result = radar_absence_debounce(
        absent=set(last_seen), last_seen_by_symbol=last_seen, now=now
    )
    assert result == {"STALE_USDT"}
